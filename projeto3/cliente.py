# cliente.py — Interface do Cliente IoT (Consumidor de mensagens)
#
# PAPEL NO MOM:
#   Este programa é o CONSUMIDOR (Subscriber).
#   Ele se conecta ao broker RabbitMQ e:
#
#   1. Escuta o padrão 'registro.#' para descobrir quais sensores
#      estão ativos (cada sensor anuncia a si mesmo periodicamente).
#
#   2. Apresenta a lista de sensores descobertos com checkboxes.
#
#   3. Quando o usuário clica "Assinar", cria bindings no exchange
#      'sensores' para os tópicos selecionados ('dados.{tipo}.{id}')
#      e exibe as mensagens em tempo real.
#
# WILDCARDS do tipo topic:
#   '#' → substitui zero ou mais palavras na routing key
#   '*' → substitui exatamente uma palavra

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import pika
import json
from datetime import datetime

# ══════════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ══════════════════════════════════════════════════════════════

RABBITMQ_HOST = 'localhost'
EXCHANGE      = 'sensores'


# ══════════════════════════════════════════════════════════════
# JANELA DO CLIENTE
# ══════════════════════════════════════════════════════════════

class ClienteApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Cliente IoT — Monitor de Sensores")
        self.root.geometry("780x520")
        self.root.configure(bg='#f0f0f0')

        # Sensores descobertos via 'registro.#'
        # chave: routing_key de dados (ex: "dados.temperatura.sensor_A")
        # valor: dict com id, tipo, unidade
        self._sensores = {}

        # Checkboxes da lista de sensores {routing_key: BooleanVar}
        self._vars_checks = {}

        # Evento para sinalizar o thread de consumo que deve parar (thread-safe)
        self._stop_consumo = threading.Event()

        self._construir_gui()

        # Inicia a descoberta automática de sensores em background
        threading.Thread(target=self._loop_descoberta, daemon=True).start()

    # ──────────────────────────────────────────────────────────
    # CONSTRUÇÃO DA INTERFACE
    # ──────────────────────────────────────────────────────────

    def _construir_gui(self):
        # ── Título ──
        tk.Label(self.root, text="📡  CLIENTE IoT — Monitor de Sensores",
                 font=("Arial", 14, "bold"), bg='#673AB7', fg='white').pack(
                 fill='x', ipady=8)

        # ── Frame principal: 2 colunas ──
        frame_main = tk.Frame(self.root, bg='#f0f0f0')
        frame_main.pack(fill='both', expand=True, padx=10, pady=8)

        # ── Coluna esquerda: sensores disponíveis ──
        frame_esq = tk.LabelFrame(frame_main, text=" Sensores Disponíveis ",
                                   bg='#f0f0f0', font=("Arial", 9, "bold"), width=220)
        frame_esq.pack(side='left', fill='y', padx=(0, 6))
        frame_esq.pack_propagate(False)

        tk.Label(frame_esq,
                 text="Detectados automaticamente\nquando os sensores publicam:",
                 bg='#f0f0f0', fg='#555', font=("Arial", 8),
                 justify='left').pack(anchor='w', padx=6, pady=(4, 0))

        # Área rolável para os checkboxes
        self._frame_checks = tk.Frame(frame_esq, bg='#f0f0f0')
        self._frame_checks.pack(fill='both', expand=True, padx=6, pady=4)

        self._lbl_aguardando = tk.Label(
            self._frame_checks,
            text="Aguardando sensores...",
            fg='gray', bg='#f0f0f0', font=("Arial", 9, "italic"))
        self._lbl_aguardando.pack(anchor='w')

        # Botão assinar
        self.btn_assinar = tk.Button(
            frame_esq, text="✔  Assinar Selecionados",
            command=self._assinar,
            state='disabled',
            bg='#673AB7', fg='white', font=("Arial", 10, "bold"))
        self.btn_assinar.pack(fill='x', padx=6, pady=6)

        # ── Coluna direita: mensagens ──
        frame_dir = tk.LabelFrame(frame_main, text=" Mensagens Recebidas ",
                                   bg='#f0f0f0', font=("Arial", 9, "bold"))
        frame_dir.pack(side='left', fill='both', expand=True)

        # Cabeçalho fixo
        tk.Label(frame_dir,
                 text=f"{'Horário':<10} {'Sensor':<14} {'Tipo':<13} {'Valor':>10}  Status",
                 font=("Courier", 9), bg='#e0e0e0', anchor='w').pack(
                 fill='x', padx=4, pady=(4, 0))

        self.area_msgs = scrolledtext.ScrolledText(
            frame_dir, state='disabled',
            font=("Courier", 9), bg='#1e1e1e', fg='#e0e0e0')
        self.area_msgs.pack(fill='both', expand=True, padx=4, pady=4)

        # Tags de cores para as mensagens
        self.area_msgs.tag_config('alerta', foreground='#ff4444')
        self.area_msgs.tag_config('normal', foreground='#00ff00')
        self.area_msgs.tag_config('info',   foreground='#aaaaaa')

    # ──────────────────────────────────────────────────────────
    # LOG DE MENSAGENS (thread-safe)
    # ──────────────────────────────────────────────────────────

    def _log(self, texto, tag='normal'):
        def _write():
            self.area_msgs.config(state='normal')
            self.area_msgs.insert('end', texto + '\n', tag)
            self.area_msgs.see('end')
            self.area_msgs.config(state='disabled')
        self.root.after(0, _write)

    # ──────────────────────────────────────────────────────────
    # DESCOBERTA DE SENSORES (thread background)
    # ──────────────────────────────────────────────────────────

    def _loop_descoberta(self):
        """
        Fica escutando 'registro.#' no exchange 'sensores'.
        Cada vez que um sensor envia seu registro, ele é adicionado
        à lista de checkboxes na GUI (se ainda não estiver lá).
        """
        try:
            conn = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST))
            ch = conn.channel()
            ch.exchange_declare(exchange=EXCHANGE, exchange_type='topic', durable=True)

            # Fila exclusiva e temporária: destruída quando este cliente fechar
            result = ch.queue_declare(queue='', exclusive=True)
            fila   = result.method.queue

            # Bind com 'registro.#': recebe anúncios de qualquer sensor
            ch.queue_bind(exchange=EXCHANGE, queue=fila, routing_key='registro.#')

            self._log("[info] Aguardando anúncios de sensores...", 'info')

            def _on_registro(ch, method, props, body):
                info = json.loads(body)
                # Converte routing key de registro → dados
                # Ex: "registro.temperatura.A" → "dados.temperatura.A"
                rk_dados = method.routing_key.replace('registro.', 'dados.', 1)
                self._adicionar_sensor(rk_dados, info)

            ch.basic_consume(queue=fila, on_message_callback=_on_registro, auto_ack=True)
            ch.start_consuming()

        except Exception as e:
            self._log(f"[erro] Descoberta: {e}", 'alerta')

    def _adicionar_sensor(self, rk_dados, info):
        """
        Adiciona um sensor novo à lista de checkboxes.
        Chamado pelo thread de descoberta; atualiza a GUI via root.after.
        """
        if rk_dados in self._sensores:
            return  # sensor já está na lista

        self._sensores[rk_dados] = info

        def _update():
            # Remove label "Aguardando..." na primeira vez
            if self._lbl_aguardando:
                self._lbl_aguardando.destroy()
                self._lbl_aguardando = None

            var = tk.BooleanVar()
            self._vars_checks[rk_dados] = var

            sid     = info.get('id', '?')
            tipo    = info.get('tipo', '?')
            unidade = info.get('unidade', '')

            cb = tk.Checkbutton(
                self._frame_checks,
                text=f"{sid}  ({tipo} / {unidade})",
                variable=var,
                bg='#f0f0f0', anchor='w',
                font=("Arial", 9)
            )
            cb.pack(fill='x', anchor='w')

            self.btn_assinar.config(state='normal')
            self._log(f"[info] Sensor descoberto: {sid} ({tipo})", 'info')

        self.root.after(0, _update)

    # ──────────────────────────────────────────────────────────
    # ASSINATURA DE TÓPICOS
    # ──────────────────────────────────────────────────────────

    def _assinar(self):
        """
        Lê quais checkboxes estão marcados e inicia uma nova
        thread de consumo para os tópicos selecionados.
        """
        # Para o consumo anterior SEMPRE que o botão é clicado
        self._stop_consumo.set()

        selecionados = [rk for rk, var in self._vars_checks.items() if var.get()]

        if not selecionados:
            self._log("[info] Assinatura cancelada.", 'info')
            return

        self._log(f"\n[info] Assinando {len(selecionados)} tópico(s)...", 'info')
        for rk in selecionados:
            info = self._sensores.get(rk, {})
            self._log(f"[info]   → {rk}  ({info.get('id', '')})", 'info')

        threading.Thread(
            target=self._loop_consumo,
            args=(selecionados,),
            daemon=True
        ).start()

    def _loop_consumo(self, routing_keys):
        """
        Conecta ao RabbitMQ, cria uma fila temporária, faz o bind
        para cada tópico selecionado e fica aguardando mensagens.

        Usa process_data_events em loop (em vez de start_consuming) para
        permitir parada segura via threading.Event sem problemas de thread-safety.
        """
        # Reseta o sinal de parada para esta nova assinatura
        self._stop_consumo.clear()

        try:
            conn = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST))
            ch = conn.channel()
            ch.exchange_declare(exchange=EXCHANGE, exchange_type='topic', durable=True)

            # Fila exclusiva: cada cliente tem a sua própria fila
            # Isso garante que todos os clientes recebam uma cópia das mensagens
            result = ch.queue_declare(queue='', exclusive=True)
            fila   = result.method.queue

            # Cria um binding para cada sensor selecionado
            for rk in routing_keys:
                ch.queue_bind(exchange=EXCHANGE, queue=fila, routing_key=rk)

            ch.basic_consume(queue=fila, on_message_callback=self._on_mensagem,
                             auto_ack=True)
            self._log("[info] Aguardando mensagens...\n", 'info')

            # Loop seguro: verifica o evento de parada a cada 1 segundo
            while not self._stop_consumo.is_set():
                conn.process_data_events(time_limit=1)

        except Exception as e:
            self._log(f"[erro] {e}", 'alerta')
        finally:
            try:
                conn.close()   # sempre fecha a conexão ao terminar
            except Exception:
                pass

    def _on_mensagem(self, ch, method, props, body):
        """
        Callback chamado automaticamente pelo RabbitMQ a cada mensagem.
        Formata e exibe na área de mensagens da GUI.
        """
        dados = json.loads(body)

        em_alerta = dados.get('alerta', False)

        linha = (
            f"[{dados['timestamp']}]  "
            f"{dados['id']:<12}  "
            f"{dados['tipo']:<12}  "
            f"{str(dados['valor']):>7}{dados['unidade']}"
        )
        if em_alerta:
            linha += (f"  ⚠ ALERTA!  "
                      f"(min:{dados['lim_min']} / max:{dados['lim_max']})")

        self._log(linha, 'alerta' if em_alerta else 'normal')


# ══════════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    root = tk.Tk()
    app  = ClienteApp(root)
    root.mainloop()
