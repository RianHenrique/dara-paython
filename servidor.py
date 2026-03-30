# servidor.py — Servidor TCP e processamento de eventos do jogo
# Gerencia conexões, recebe ações dos jogadores, valida e retransmite.

import socket
import threading
import json
import time

from jogo import criar_estado, obter_linhas, formaria_linha

PORTA = 5000


# ══════════════════════════════════════════════════════════════
# SERVIDOR TCP
# ══════════════════════════════════════════════════════════════

class Servidor:
    def __init__(self):
        self.lock = threading.RLock()  # RLock: a mesma thread pode entrar várias vezes
        self._resetar()

    def _resetar(self):
        """Reinicia tudo para uma nova partida."""
        self.estado         = criar_estado()
        self.clientes       = {}                # {numero: socket}
        self.evento_fim     = threading.Event() # sinaliza quando a partida acabou
        self.fim_sinalizado = False             # evita encerrar duas vezes

    # ──────────────────────────────────────────────────────────
    # INICIALIZAÇÃO DO SERVIDOR
    # ──────────────────────────────────────────────────────────

    def iniciar(self):
        """
        Abre o socket TCP e fica em loop aceitando partidas.
        A cada partida: aguarda 2 jogadores → joga → reinicia.
        """
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Cria socket. AF_INET = usa IPv4 / SOCK_STREM = TCP
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # Permite reusar a porta imediatamente apos fechar servidor
        srv.bind(('', PORTA))
        srv.listen(5)
        srv.settimeout(1.0)  # verifica Ctrl+C a cada 1 segundo
        
        # Abre socket UDP temporário e "conecta" num IP externo (sem enviar nada)
        # só pra descobrir qual interface de rede o sistema usaria — pega o IP do Wi-Fi
        try:
            temp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            temp.connect(("8.8.8.8", 80))
            ip_local = temp.getsockname()[0]
            temp.close()
        except Exception:
            ip_local = socket.gethostbyname(socket.gethostname())
        print(f"Servidor rodando em {ip_local}:{PORTA}")
        print("Pressione Ctrl+C para encerrar.\n")

        #Loop Infinito de partidas:
        try: 
            while True:
                self._resetar()
                print("\n── Nova partida: aguardando 2 jogadores... ──")

                numero = 1
                while numero <= 2:

                    while True:
                        try:
                            conn, addr = srv.accept()
                            break
                        except socket.timeout:
                            continue  # volta a tentar, permitindo Ctrl+C

                    self.clientes[numero] = conn #Guarda o socket do jogador
                    print(f"  Jogador {numero} conectado: {addr}")
                    self.enviar(conn, {"tipo": "conectado", "jogador": numero})

                    if numero == 2:
                        # Dois jogadores prontos — começa a partida
                        self.broadcast({"tipo": "inicio",
                                        "mensagem": "Partida iniciada! Jogador 1 começa."})
                        self.broadcast_estado()
                        for j in [1, 2]:
                            threading.Thread(target=self.escutar,
                                             args=(j,), daemon=True).start() # Cria e inicia uma thread separada rodando a função escutar
                    else:
                        self.enviar(conn, {"tipo": "aguardando",
                                           "mensagem": "Aguardando o segundo jogador..."})
                        
                    numero += 1

                # Aguarda o sinal de fim da partida (vitória, desistência ou desconexão)
                self.evento_fim.wait()
                print("  Partida encerrada. Reiniciando em 3 s...")
                time.sleep(3)   # tempo para os clientes receberem o "fim"
                for conn in self.clientes.values():
                    try:
                        conn.close()
                    except Exception:
                        pass

        except KeyboardInterrupt:
            print("\nServidor encerrado.")
            srv.close()

    # ──────────────────────────────────────────────────────────
    # RECEPÇÃO DE MENSAGENS
    # ──────────────────────────────────────────────────────────

    def escutar(self, numero):
        """
        Thread dedicada a um jogador.
        Fica em loop lendo dados do socket e chamando processar().
        TCP é um stream, então acumula num buffer e divide por '\n'.
        """
        conn   = self.clientes[numero]
        buffer = ""
        while True:
            try:
                dados = conn.recv(4096).decode("utf-8")
                if not dados:
                    break                       # cliente fechou a conexão
                buffer += dados
                while "\n" in buffer:
                    linha, buffer = buffer.split("\n", 1) #linha é o que tem antes do primeiro \n.
                    if linha.strip():
                        msg = json.loads(linha)
                        self.processar(msg, numero)
            except Exception:
                break

        # Se o jogo ainda estava em andamento E essa conexão ainda é da partida atual
        # (self.clientes.get(numero) is conn evita que threads de partidas antigas
        #  encerrem a partida nova após o servidor reiniciar)
        if not self.fim_sinalizado and self.clientes.get(numero) is conn:
            vencedor = 2 if numero == 1 else 1
            print(f"  Jogador {numero} desconectou durante a partida.")
            self.encerrar_partida({
                "tipo":     "fim",
                "vencedor": vencedor,
                "mensagem": f"Jogador {numero} desconectou! Jogador {vencedor} venceu!",
            })

    # ──────────────────────────────────────────────────────────
    # PROCESSAMENTO DE EVENTOS
    # ──────────────────────────────────────────────────────────

    def processar(self, msg, numero):
        """
        Interpreta uma mensagem recebida de um jogador e atualiza o estado.
        Todas as validações de regra ficam aqui.
        """
        with self.lock:
            if self.fim_sinalizado:
                return

            tipo   = msg.get("tipo")
            estado = self.estado

            # ── Chat: retransmite para os dois jogadores ───────
            if tipo == "chat":
                self.broadcast({"tipo": "chat", "jogador": numero,
                                "mensagem": msg["mensagem"]})
                return

            # ── Desistir ───────────────────────────────────────
            if tipo == "desistir":
                if estado["vencedor"]:
                    return
                vencedor = 2 if numero == 1 else 1
                self.encerrar_partida({
                    "tipo":     "fim",
                    "vencedor": vencedor,
                    "mensagem": (f"Jogador {numero} desistiu. "
                                 f"Jogador {vencedor} venceu!"),
                })
                return

            if estado["vencedor"]:
                return  # partida já encerrada, ignora demais ações

            # ══════════════════════════════════════════════════
            # FASE DE POSICIONAMENTO
            # Jogadores colocam as 12 peças alternadamente.
            # Regra: não pode formar trio de 3 ao colocar.
            # ══════════════════════════════════════════════════
            if tipo == "colocar":
                if estado["fase"] != "posicionamento":
                    return
                if estado["turno"] != numero:
                    self._erro(numero, "Não é sua vez")
                    return

                linha, coluna = msg["linha"], msg["coluna"]

                if estado["tabuleiro"][linha][coluna] != 0:
                    self._erro(numero, "Casa já ocupada")
                    return

                #Impede formar trio.
                if formaria_linha(estado["tabuleiro"], numero, linha, coluna):
                    self._erro(numero,
                               "Não é permitido formar 3 em linha no posicionamento!")
                    return

                # Aplica a jogada em uma cópia do tabuleiro
                novo = [row[:] for row in estado["tabuleiro"]]
                novo[linha][coluna] = numero
                estado["tabuleiro"] = novo

                estado["pecas"][numero - 1]             -= 1
                estado["pecas_no_tabuleiro"][numero - 1] += 1

                # Todas as peças posicionadas? Passa para a próxima fase
                if estado["pecas"][0] == 0 and estado["pecas"][1] == 0:
                    estado["fase"] = "captura"
                    self.broadcast_estado(
                        "Todas as peças posicionadas! Fase de movimentação iniciada.")
                else:
                    estado["turno"] = 2 if numero == 1 else 1
                    self.broadcast_estado()
                return

            # ══════════════════════════════════════════════════
            # FASE DE CAPTURA — MOVER PEÇA
            # Jogador move uma peça para casa adjacente.
            # Se formar trio novo, precisa capturar uma peça do oponente.
            # ══════════════════════════════════════════════════
            if tipo == "mover":
                if estado["fase"] != "captura":
                    return
                if estado["turno"] != numero:
                    self._erro(numero, "Não é sua vez")
                    return
                if estado["deve_capturar"]:
                    self._erro(numero,
                               "Você precisa capturar uma peça do oponente primeiro!")
                    return

                de_l, de_c     = msg["de_linha"], msg["de_col"]
                para_l, para_c = msg["para_linha"], msg["para_col"]

                if estado["tabuleiro"][de_l][de_c] != numero:
                    self._erro(numero, "Essa peça não é sua")
                    return
                if estado["tabuleiro"][para_l][para_c] != 0:
                    self._erro(numero, "Casa ocupada!")
                    return

                dl = abs(para_l - de_l)
                dc = abs(para_c - de_c)
                if not ((dl == 1 and dc == 0) or (dl == 0 and dc == 1)):
                    self._erro(numero,
                               "Mova para uma casa adjacente (horizontal ou vertical)!")
                    return

                # Guarda os trios existentes ANTES do movimento
                linhas_antes = obter_linhas(estado["tabuleiro"], numero)

                # Aplica o movimento em uma cópia
                novo = [row[:] for row in estado["tabuleiro"]]
                novo[de_l][de_c]     = 0
                novo[para_l][para_c] = numero
                estado["tabuleiro"]  = novo

                # Compara com os trios DEPOIS — só conta os realmente novos
                linhas_depois = obter_linhas(estado["tabuleiro"], numero)
                novas_linhas  = linhas_depois - linhas_antes

                if novas_linhas:
                    # Formou trio novo → deve capturar uma peça do oponente
                    estado["deve_capturar"] = numero
                    self.broadcast_estado(
                        f"Jogador {numero} formou uma linha! "
                        "Capture uma peça do oponente.")
                else:
                    # Movimento normal → passa a vez
                    estado["turno"] = 2 if numero == 1 else 1
                    self.broadcast_estado()
                return

            # ══════════════════════════════════════════════════
            # FASE DE CAPTURA — CAPTURAR PEÇA DO OPONENTE
            # Executado após formar um trio: remove uma peça do oponente.
            # Verifica vitória: oponente com ≤ 2 peças → fim de jogo.
            # ══════════════════════════════════════════════════
            if tipo == "capturar":
                if estado["fase"] != "captura":
                    return
                if estado["deve_capturar"] != numero:
                    self._erro(numero, "Você não precisa capturar agora")
                    return

                linha, coluna = msg["linha"], msg["coluna"]
                oponente = 2 if numero == 1 else 1

                if estado["tabuleiro"][linha][coluna] != oponente:
                    self._erro(numero, "Selecione uma peça do oponente para capturar!")
                    return

                # Remove a peça do oponente
                novo = [row[:] for row in estado["tabuleiro"]]
                novo[linha][coluna] = 0
                estado["tabuleiro"] = novo
                estado["pecas_no_tabuleiro"][oponente - 1] -= 1

                # Verifica vitória: oponente com 2 ou menos peças?
                # Sem verificação de fase — a condição é simples:
                if estado["pecas_no_tabuleiro"][oponente - 1] <= 2:
                    # Envia o tabuleiro atualizado primeiro (peça sumindo)
                    # antes de mandar o "fim", assim o cliente vê a captura
                    # antes do popup de vitória aparecer
                    self.broadcast_estado(f"Jogador {numero} capturou uma peça!")
                    estado["vencedor"] = numero
                    estado["fase"]     = "encerrado"
                    self.encerrar_partida({
                        "tipo":     "fim",
                        "vencedor": numero,
                        "mensagem": f"Jogador {numero} venceu!",
                    })
                    return

                # Captura feita, passa a vez
                estado["deve_capturar"] = None
                estado["turno"]         = 2 if numero == 1 else 1
                self.broadcast_estado(f"Jogador {numero} capturou uma peça!")
                return

    # ──────────────────────────────────────────────────────────
    # ENCERRAMENTO DE PARTIDA
    # ──────────────────────────────────────────────────────────

    def encerrar_partida(self, dados_fim):
        """
        Envia a mensagem de fim para os dois jogadores e sinaliza
        o loop principal para reiniciar o servidor.
        """
        with self.lock:
            if self.fim_sinalizado:
                return                          # já foi encerrado, ignora
            self.fim_sinalizado = True
        self.broadcast(dados_fim)
        threading.Timer(1.0, self.evento_fim.set).start() #1 segundo pra da tempo dos cliente receberem a mensagem de fim.

    # ──────────────────────────────────────────────────────────
    # ENVIO DE MENSAGENS
    # ──────────────────────────────────────────────────────────

    def broadcast_estado(self, mensagem=""):
        """Envia o estado completo do jogo para os dois jogadores."""
        self.broadcast({
            "tipo":               "estado",
            "tabuleiro":          self.estado["tabuleiro"],
            "turno":              self.estado["turno"],
            "fase":               self.estado["fase"],
            "pecas":              self.estado["pecas"],
            "pecas_no_tabuleiro": self.estado["pecas_no_tabuleiro"],
            "deve_capturar":      self.estado["deve_capturar"],
            "mensagem":           mensagem,
        })

    def broadcast(self, dados):
        """Envia uma mensagem para todos os jogadores conectados."""
        for conn in self.clientes.values():
            self.enviar(conn, dados)

    def enviar(self, conn, dados):
        """
        Serializa os dados em JSON e envia pelo socket TCP.
        Cada mensagem termina com '\n' para delimitar no buffer do receptor.
        """
        try:
            conn.sendall((json.dumps(dados) + "\n").encode("utf-8"))
        except Exception:
            pass

    def _erro(self, numero, texto):
        """Envia uma mensagem de erro somente para um jogador."""
        self.enviar(self.clientes[numero], {"tipo": "erro", "mensagem": texto})


# ──────────────────────────────────────────────────────────────
# PONTO DE ENTRADA
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    Servidor().iniciar()
