# servidor.py — Servidor de Mensagens Offline
#
# ARQUITETURA:
#   Este servidor combina duas tecnologias:
#   1. Pyro5 (RPC) — expõe métodos remotos que os clientes chamam via rede
#   2. RabbitMQ (MOM) — armazena mensagens offline em filas persistentes
#
# FLUXO:
#   Cliente A envia msg para B
#     → B online?  SIM → msg vai para lista de pendentes de B (entregue no próximo poll)
#     → B offline? NÃO → msg vai para fila RabbitMQ de B (persistente)
#   B volta online (login) → servidor drena a fila RabbitMQ e entrega as msgs guardadas

import socket
import threading
import json
import pika
import Pyro5.api
import Pyro5.server
from datetime import datetime

PORTA         = 9091
RABBITMQ_HOST = 'localhost'


# ══════════════════════════════════════════════════════════════
# SERVIDOR
# ══════════════════════════════════════════════════════════════

@Pyro5.api.expose
class ServidorMensagens:
    def __init__(self):
        self.lock = threading.RLock()
        # Estrutura de cada cliente:
        # { 'online': bool, 'contatos': [str], 'pendentes': [dict] }
        self.clientes = {}

    # ──────────────────────────────────────────────────────────
    # HELPERS RABBITMQ
    # ──────────────────────────────────────────────────────────

    def _criar_fila(self, nome):
        """Cria fila durable no RabbitMQ para o cliente."""
        conn = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
        ch   = conn.channel()
        # durable=True → fila sobrevive ao restart do RabbitMQ
        ch.queue_declare(queue=f'msg.{nome}', durable=True)
        conn.close()
        print(f"[RabbitMQ] Fila criada: msg.{nome}")

    def _publicar_offline(self, destinatario, payload):
        """Publica mensagem na fila offline do destinatário."""
        conn = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
        ch   = conn.channel()
        ch.queue_declare(queue=f'msg.{destinatario}', durable=True)
        ch.basic_publish(
            exchange='',                        # exchange padrão (direct)
            routing_key=f'msg.{destinatario}', # nome da fila como routing key
            body=json.dumps(payload),
            properties=pika.BasicProperties(delivery_mode=2)  # mensagem persistente
        )
        conn.close()

    def _drenar_fila(self, nome):
        """Lê e remove todas as mensagens da fila RabbitMQ do cliente."""
        msgs = []
        try:
            conn = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
            ch   = conn.channel()
            ch.queue_declare(queue=f'msg.{nome}', durable=True)
            while True:
                # basic_get retorna None quando a fila está vazia
                method, props, body = ch.basic_get(queue=f'msg.{nome}', auto_ack=True)
                if method is None:
                    break
                m = json.loads(body)
                m['offline'] = True  # marca que essa msg foi armazenada offline
                msgs.append(m)
            conn.close()
        except Exception as e:
            print(f"[erro] drenar fila: {e}")
        return msgs

    # ──────────────────────────────────────────────────────────
    # MÉTODOS RPC — chamados pelos clientes via Pyro5
    # ──────────────────────────────────────────────────────────

    def registrar(self, nome):
        """
        Registra um novo cliente e cria sua fila no RabbitMQ.
        Chamado uma vez quando o cliente entra pela primeira vez.
        """
        with self.lock:
            if nome in self.clientes:
                return {'ok': True, 'novo': False}
            self.clientes[nome] = {
                'online':    False,
                'contatos':  [],
                'pendentes': [],
            }
        # Cria a fila FORA do lock — pika não deve segurar o lock
        self._criar_fila(nome)
        print(f"[+] Novo cliente registrado: {nome}")
        return {'ok': True, 'novo': True}

    def login(self, nome):
        """
        Marca o cliente como online e entrega as mensagens
        que ficaram na fila RabbitMQ enquanto estava offline.
        """
        with self.lock:
            if nome not in self.clientes:
                return {'ok': False, 'erro': 'Cliente não registrado'}
            self.clientes[nome]['online'] = True

        # Drena a fila FORA do lock — operação pika pode demorar
        msgs_offline = self._drenar_fila(nome)

        with self.lock:
            self.clientes[nome]['pendentes'].extend(msgs_offline)

        print(f"[online] {nome} ({len(msgs_offline)} msgs offline entregues)")
        return {'ok': True}

    def logout(self, nome):
        """Marca o cliente como offline."""
        with self.lock:
            if nome in self.clientes:
                self.clientes[nome]['online'] = False
                print(f"[offline] {nome}")
            return {'ok': True}

    def enviar(self, remetente, destinatario, mensagem):
        """
        Envia mensagem de remetente para destinatário.
        - Destinatário online  → adiciona à lista de pendentes (entrega instantânea via poll)
        - Destinatário offline → publica na fila RabbitMQ (entregue quando voltar)
        """
        payload = {
            'de':        remetente,
            'msg':       mensagem,
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'offline':   False,
        }

        with self.lock:
            if destinatario not in self.clientes:
                return {'ok': False, 'erro': 'Destinatário não encontrado no servidor'}
            if self.clientes[destinatario]['online']:
                self.clientes[destinatario]['pendentes'].append(payload)
                print(f"[msg online] {remetente} → {destinatario}")
                return {'ok': True, 'entregue': 'online'}
            # else: sai do lock e publica no RabbitMQ fora dele

        # Publica FORA do lock — pika não deve segurar o lock
        self._publicar_offline(destinatario, payload)
        print(f"[msg offline] {remetente} → {destinatario} (fila RabbitMQ)")
        return {'ok': True, 'entregue': 'offline'}

    def buscar_mensagens(self, nome):
        """
        Retorna e limpa as mensagens pendentes do cliente.
        Chamado periodicamente pelo cliente.
        """
        with self.lock:
            if nome not in self.clientes:
                return []
            msgs = list(self.clientes[nome]['pendentes'])
            self.clientes[nome]['pendentes'] = []
            return msgs

    def listar_contatos(self, nome):
        """Retorna contatos do cliente com status online/offline atual."""
        with self.lock:
            if nome not in self.clientes:
                return []
            return [
                {
                    'nome':       c,
                    'online':     self.clientes.get(c, {}).get('online', False),
                    'registrado': c in self.clientes,
                }
                for c in self.clientes[nome]['contatos']
            ]

    def adicionar_contato(self, nome, contato):
        """Adiciona contato à lista do cliente."""
        with self.lock:
            if nome not in self.clientes:
                return {'ok': False, 'erro': 'Você não está registrado'}
            if contato == nome:
                return {'ok': False, 'erro': 'Não pode adicionar a si mesmo'}
            if contato not in self.clientes:
                return {'ok': False, 'erro': f'"{contato}" não está registrado no servidor'}
            if contato in self.clientes[nome]['contatos']:
                return {'ok': False, 'erro': 'Contato já está na lista'}
            self.clientes[nome]['contatos'].append(contato)
            return {'ok': True}

    def remover_contato(self, nome, contato):
        """Remove contato da lista do cliente."""
        with self.lock:
            if nome in self.clientes and contato in self.clientes[nome]['contatos']:
                self.clientes[nome]['contatos'].remove(contato)
            return {'ok': True}

    def listar_clientes(self):
        """Lista todos os clientes registrados (usado para testar conexão)."""
        with self.lock:
            return list(self.clientes.keys())


# ══════════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    try:
        temp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        temp.connect(('8.8.8.8', 80))
        ip_local = temp.getsockname()[0]
        temp.close()
    except Exception:
        ip_local = socket.gethostbyname(socket.gethostname())

    servidor = ServidorMensagens()
    daemon   = Pyro5.server.Daemon(host='0.0.0.0', port=PORTA)
    daemon.register(servidor, objectId='mensageiro.servidor')

    print(f"Servidor rodando em {ip_local}:{PORTA}")
    print("Aguardando clientes... (Ctrl+C para encerrar)\n")

    try:
        daemon.requestLoop()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
