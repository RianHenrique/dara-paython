# servidor.py — Servidor RPC com Pyro5
# Expõe os métodos do jogo como chamadas remotas.

import socket
import threading
import Pyro5.api
import Pyro5.server

from jogo import criar_estado, obter_linhas, formaria_linha

PORTA = 9090


# ══════════════════════════════════════════════════════════════
# SERVIDOR RPC
# ══════════════════════════════════════════════════════════════

@Pyro5.api.expose
class ServidorDara:
    def __init__(self):
        self.lock = threading.RLock()
        self.estado              = criar_estado()
        self.jogadores_conectados = 0
        self.historico_chat      = []
        self.mensagem_sistema    = ""
        self.msg_seq             = 0   # incrementa a cada mudança de estado

    # ──────────────────────────────────────────────────────────
    # ENTRADA NA PARTIDA
    # ──────────────────────────────────────────────────────────

    def entrar(self):
        """Jogador entra na partida. Retorna seu número (1 ou 2)."""
        with self.lock:
            # Qualquer jogador reconectando após partida encerrada → reset automático
            if self.estado["fase"] == "encerrado":
                self.estado               = criar_estado()
                self.historico_chat       = []
                self.mensagem_sistema     = ""
                self.msg_seq             += 1
                self.jogadores_conectados = 0

            if self.jogadores_conectados >= 2:
                return {"ok": False, "erro": "Partida cheia"}
            self.jogadores_conectados += 1
            numero = self.jogadores_conectados
            if numero == 2:
                self._set_msg("Partida iniciada! Jogador 1 começa.")
            return {"ok": True, "jogador": numero}

    # ──────────────────────────────────────────────────────────
    # POLLING — cliente chama periodicamente para ver novidades
    # ──────────────────────────────────────────────────────────

    def get_estado(self, ultimo_chat_idx=0):
        """
        Retorna o estado completo do jogo mais as mensagens de chat
        ainda não vistas pelo cliente (a partir de ultimo_chat_idx).
        """
        with self.lock:
            return {
                "tabuleiro":           self.estado["tabuleiro"],
                "turno":               self.estado["turno"],
                "fase":                self.estado["fase"],
                "pecas":               self.estado["pecas"],
                "pecas_no_tabuleiro":  self.estado["pecas_no_tabuleiro"],
                "deve_capturar":       self.estado["deve_capturar"],
                "vencedor":            self.estado["vencedor"],
                "mensagem_sistema":    self.mensagem_sistema,
                "msg_seq":             self.msg_seq,
                "jogadores_conectados": self.jogadores_conectados,
                "novas_msgs":          self.historico_chat[ultimo_chat_idx:],
            }

    # ──────────────────────────────────────────────────────────
    # AÇÕES DO JOGO
    # ──────────────────────────────────────────────────────────

    def colocar(self, numero, linha, coluna):
        """Fase de posicionamento: coloca uma peça no tabuleiro."""
        with self.lock:
            if self.estado["fase"] != "posicionamento":
                return {"ok": False, "erro": "Fase incorreta"}
            if self.estado["turno"] != numero:
                return {"ok": False, "erro": "Não é sua vez"}
            if self.estado["tabuleiro"][linha][coluna] != 0:
                return {"ok": False, "erro": "Casa já ocupada"}
            if formaria_linha(self.estado["tabuleiro"], numero, linha, coluna):
                return {"ok": False, "erro": "Não é permitido formar 3 em linha no posicionamento!"}

            novo = [row[:] for row in self.estado["tabuleiro"]]
            novo[linha][coluna] = numero
            self.estado["tabuleiro"] = novo
            self.estado["pecas"][numero - 1] -= 1
            self.estado["pecas_no_tabuleiro"][numero - 1] += 1

            if self.estado["pecas"][0] == 0 and self.estado["pecas"][1] == 0:
                self.estado["fase"] = "captura"
                self._set_msg("Todas as peças posicionadas! Fase de movimentação iniciada.")
            else:
                self.estado["turno"] = 2 if numero == 1 else 1

            return {"ok": True}

    def mover(self, numero, de_l, de_c, para_l, para_c):
        """Fase de captura: move uma peça para casa adjacente."""
        with self.lock:
            if self.estado["fase"] != "captura":
                return {"ok": False, "erro": "Fase incorreta"}
            if self.estado["turno"] != numero:
                return {"ok": False, "erro": "Não é sua vez"}
            if self.estado["deve_capturar"]:
                return {"ok": False, "erro": "Você precisa capturar uma peça do oponente primeiro!"}
            if self.estado["tabuleiro"][de_l][de_c] != numero:
                return {"ok": False, "erro": "Essa peça não é sua"}
            if self.estado["tabuleiro"][para_l][para_c] != 0:
                return {"ok": False, "erro": "Casa ocupada!"}

            dl = abs(para_l - de_l)
            dc = abs(para_c - de_c)
            if not ((dl == 1 and dc == 0) or (dl == 0 and dc == 1)):
                return {"ok": False, "erro": "Mova para uma casa adjacente (horizontal ou vertical)!"}

            linhas_antes = obter_linhas(self.estado["tabuleiro"], numero)

            novo = [row[:] for row in self.estado["tabuleiro"]]
            novo[de_l][de_c] = 0
            novo[para_l][para_c] = numero
            self.estado["tabuleiro"] = novo

            linhas_depois = obter_linhas(self.estado["tabuleiro"], numero)
            novas_linhas  = linhas_depois - linhas_antes

            if novas_linhas:
                self.estado["deve_capturar"] = numero
                self._set_msg(f"Jogador {numero} formou uma linha! Capture uma peça do oponente.")
            else:
                self.estado["turno"] = 2 if numero == 1 else 1

            return {"ok": True}

    def capturar(self, numero, linha, coluna):
        """Remove uma peça do oponente após formar trio."""
        with self.lock:
            if self.estado["fase"] != "captura":
                return {"ok": False, "erro": "Fase incorreta"}
            if self.estado["deve_capturar"] != numero:
                return {"ok": False, "erro": "Você não precisa capturar agora"}

            oponente = 2 if numero == 1 else 1
            if self.estado["tabuleiro"][linha][coluna] != oponente:
                return {"ok": False, "erro": "Selecione uma peça do oponente para capturar!"}

            novo = [row[:] for row in self.estado["tabuleiro"]]
            novo[linha][coluna] = 0
            self.estado["tabuleiro"] = novo
            self.estado["pecas_no_tabuleiro"][oponente - 1] -= 1

            if self.estado["pecas_no_tabuleiro"][oponente - 1] <= 2:
                self.estado["vencedor"] = numero
                self.estado["fase"]     = "encerrado"
                self._set_msg(f"Jogador {numero} venceu!")
            else:
                self.estado["deve_capturar"] = None
                self.estado["turno"]         = 2 if numero == 1 else 1
                self._set_msg(f"Jogador {numero} capturou uma peça!")

            return {"ok": True}

    def desistir(self, numero):
        """Jogador desiste — oponente vence."""
        with self.lock:
            if self.estado["vencedor"]:
                return {"ok": False, "erro": "Jogo já encerrado"}
            vencedor = 2 if numero == 1 else 1
            self.estado["vencedor"] = vencedor
            self.estado["fase"]     = "encerrado"
            self._set_msg(f"Jogador {numero} desistiu. Jogador {vencedor} venceu!")
            return {"ok": True}

    def chat(self, numero, mensagem):
        """Adiciona mensagem ao histórico de chat."""
        with self.lock:
            self.historico_chat.append({"jogador": numero, "mensagem": mensagem})
            return {"ok": True}

    def nova_partida(self):
        """Reseta o servidor para uma nova partida."""
        with self.lock:
            self.estado               = criar_estado()
            self.jogadores_conectados = 0
            self.historico_chat       = []
            self.mensagem_sistema     = ""
            self.msg_seq             += 1
        return {"ok": True}

    # ──────────────────────────────────────────────────────────
    # AUXILIAR
    # ──────────────────────────────────────────────────────────

    def _set_msg(self, texto):
        self.mensagem_sistema = texto
        self.msg_seq += 1


# ══════════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        temp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        temp.connect(("8.8.8.8", 80))
        ip_local = temp.getsockname()[0]
        temp.close()
    except Exception:
        ip_local = socket.gethostbyname(socket.gethostname())

    servidor = ServidorDara()
    daemon   = Pyro5.server.Daemon(host="0.0.0.0", port=PORTA)
    daemon.register(servidor, objectId="dara.servidor")

    print(f"Servidor RPC rodando em {ip_local}:{PORTA}")
    print("Pressione Ctrl+C para encerrar.\n")

    try:
        daemon.requestLoop()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
