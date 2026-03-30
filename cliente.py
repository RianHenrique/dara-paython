import socket
import threading
import json
import tkinter as tk
from tkinter import messagebox, scrolledtext

# ─── Constantes ───────────────────────────────────────────────
LINHAS       = 5
COLUNAS      = 6
PORTA        = 5000
TAMANHO_CASA = 80

CORES = {
    0:             "#d4b896",
    1:             "#ffffff",
    2:             "#222222",
    "selecionado": "#f0e040",
    "tabuleiro":   "#8b6914",
}


# ─── Cliente ──────────────────────────────────────────────────
class ClienteDara:
    def __init__(self):
        self.sock           = None
        self.meu_numero     = None
        self.estado         = None
        self.selecionado    = None
        self.buffer         = ""
        self.jogo_encerrado = False
        self._ultimo_ip     = "127.0.0.1"   # guardado para reconectar

        self.janela = tk.Tk()
        self.janela.title("Jogo Dara")
        self.janela.resizable(False, False)

        self.construir_tela_conexao()
        self.janela.mainloop()

    # ══════════════════════════════════════════════════════════
    # TELA DE CONEXÃO
    # ══════════════════════════════════════════════════════════
    def construir_tela_conexao(self, ip="127.0.0.1"):
        self.frame_conexao = tk.Frame(self.janela, padx=30, pady=30)
        self.frame_conexao.pack()

        tk.Label(self.frame_conexao, text="JOGO DARA",
                 font=("Arial", 22, "bold")).pack(pady=10)
        tk.Label(self.frame_conexao, text="IP do Servidor:",
                 font=("Arial", 11)).pack()

        self.entrada_ip = tk.Entry(self.frame_conexao, width=22, font=("Arial", 12))
        self.entrada_ip.insert(0, ip)
        self.entrada_ip.pack(pady=5)

        tk.Button(
            self.frame_conexao, text="Conectar",
            command=self.conectar,
            bg="#4CAF50", fg="white", font=("Arial", 12), width=12
        ).pack(pady=10)

        self.label_status = tk.Label(self.frame_conexao, text="",
                                     font=("Arial", 10), fg="gray")
        self.label_status.pack()

    # ----------------------------------------------------------
    def conectar(self):
        ip = self.entrada_ip.get().strip()
        self._ultimo_ip = ip
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((ip, PORTA))
            self.label_status.config(text="Conectado! Aguardando...", fg="green")
            threading.Thread(target=self.receber, daemon=True).start()
        except Exception as e:
            self.label_status.config(text=f"Erro: {e}", fg="red")

    # ══════════════════════════════════════════════════════════
    # RECEPÇÃO (thread separada)
    # ══════════════════════════════════════════════════════════
    def receber(self):
        while True:
            try:
                dados = self.sock.recv(4096).decode("utf-8")
                if not dados:
                    break
                self.buffer += dados
                while "\n" in self.buffer:
                    linha, self.buffer = self.buffer.split("\n", 1)
                    if linha.strip():
                        msg = json.loads(linha)
                        self.janela.after(0, self.processar_mensagem, msg)
            except Exception:
                break

    # ══════════════════════════════════════════════════════════
    # PROCESSAMENTO DE MENSAGENS
    # ══════════════════════════════════════════════════════════
    def processar_mensagem(self, msg):
        tipo = msg.get("tipo")

        if tipo == "conectado":
            self.meu_numero = msg["jogador"]
            self.janela.title(f"Jogo Dara — Você é o Jogador {self.meu_numero}")

        elif tipo == "aguardando":
            self.label_status.config(text=msg["mensagem"], fg="orange")

        elif tipo == "inicio":
            self.frame_conexao.destroy()
            self.construir_interface_jogo()

        elif tipo == "estado":
            self.estado = msg
            self.atualizar_interface()
            if msg.get("mensagem"):
                self.adicionar_chat(f"[Sistema] {msg['mensagem']}")

        elif tipo == "chat":
            self.adicionar_chat(f"Jogador {msg['jogador']}: {msg['mensagem']}")

        elif tipo == "erro":
            self.adicionar_chat(f"[Erro] {msg['mensagem']}")

        elif tipo == "fim":
            # Jogo encerrado: mostra resultado e botão de nova partida
            self.jogo_encerrado = True
            self.adicionar_chat(f"[Sistema] {msg['mensagem']}")
            if msg["vencedor"] == self.meu_numero:
                messagebox.showinfo("FIM DE JOGO", f"Você VENCEU!\n{msg['mensagem']}")
            else:
                messagebox.showinfo("FIM DE JOGO", f"Você perdeu.\n{msg['mensagem']}")
            self._mostrar_botao_nova_partida()

        elif tipo == "desconexao":
            # Oponente desconectou (mas o servidor já cuida da vitória via "fim")
            # Caso chegue sem "fim" antes, mostra botão também
            if not self.jogo_encerrado:
                self.jogo_encerrado = True
                self.adicionar_chat(f"[Sistema] {msg['mensagem']}")
                self._mostrar_botao_nova_partida()

    # ══════════════════════════════════════════════════════════
    # NOVA PARTIDA
    # ══════════════════════════════════════════════════════════
    def _mostrar_botao_nova_partida(self):
        """Adiciona botão 'Nova Partida' na interface atual."""
        if not hasattr(self, "_btn_nova") or not self._btn_nova:
            self._btn_nova = tk.Button(
                self.janela,
                text="Nova Partida",
                command=self._nova_partida,
                bg="#2196F3", fg="white",
                font=("Arial", 12, "bold"), width=14
            )
            self._btn_nova.pack(pady=8)

    def _nova_partida(self):
        """Fecha o socket, limpa tudo e volta à tela de conexão."""
        ip = self._ultimo_ip

        # Fecha socket atual
        try:
            self.sock.close()
        except Exception:
            pass

        # Reseta estado
        self.sock           = None
        self.meu_numero     = None
        self.estado         = None
        self.selecionado    = None
        self.buffer         = ""
        self.jogo_encerrado = False
        self._btn_nova      = None

        # Remove todos os widgets e reconstrói a tela inicial
        for widget in self.janela.winfo_children():
            widget.destroy()

        self.construir_tela_conexao(ip=ip)

    # ══════════════════════════════════════════════════════════
    # INTERFACE DO JOGO
    # ══════════════════════════════════════════════════════════
    def construir_interface_jogo(self):
        self._btn_nova = None
        frame = tk.Frame(self.janela)
        frame.pack(padx=10, pady=10)

        # Painel de informações
        frame_info = tk.Frame(frame, bd=2, relief="groove", pady=4)
        frame_info.pack(fill="x", pady=(0, 6))

        self.label_fase     = tk.Label(frame_info, text="Fase: Colocação",
                                       font=("Arial", 11, "bold"))
        self.label_fase.pack(side="left", padx=12)

        self.label_turno    = tk.Label(frame_info, text="Turno: —",
                                       font=("Arial", 11))
        self.label_turno.pack(side="left", padx=12)

        self.label_pecas    = tk.Label(frame_info, text="Para colocar: J1=12 | J2=12",
                                       font=("Arial", 11))
        self.label_pecas.pack(side="left", padx=12)

        self.label_capturas = tk.Label(frame_info, text="Capturas: J1=0 | J2=0",
                                       font=("Arial", 11))
        self.label_capturas.pack(side="left", padx=12)

        # Centro: tabuleiro + chat
        frame_centro = tk.Frame(frame)
        frame_centro.pack()

        frame_tab = tk.Frame(frame_centro)
        frame_tab.pack(side="left", padx=10)
        tk.Label(frame_tab, text="TABULEIRO", font=("Arial", 10, "bold")).pack()

        self.canvas = tk.Canvas(
            frame_tab,
            width=COLUNAS * TAMANHO_CASA,
            height=LINHAS  * TAMANHO_CASA,
            bg=CORES["tabuleiro"]
        )
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.clique_tabuleiro)

        frame_chat = tk.Frame(frame_centro, bd=2, relief="groove")
        frame_chat.pack(side="left", padx=10, fill="y")
        tk.Label(frame_chat, text="CHAT", font=("Arial", 10, "bold")).pack(pady=(4, 0))

        self.area_chat = scrolledtext.ScrolledText(
            frame_chat, width=32, height=22,
            state="disabled", wrap="word", font=("Arial", 9)
        )
        self.area_chat.pack(padx=6, pady=4)

        frame_entrada = tk.Frame(frame_chat)
        frame_entrada.pack(fill="x", padx=6, pady=4)

        self.entrada_chat = tk.Entry(frame_entrada, font=("Arial", 10))
        self.entrada_chat.pack(side="left", fill="x", expand=True)
        self.entrada_chat.bind("<Return>", self.enviar_chat)

        tk.Button(frame_entrada, text="Enviar",
                  command=self.enviar_chat).pack(side="right", padx=(4, 0))

        tk.Button(
            frame, text="Desistir",
            command=self.desistir,
            bg="#f44336", fg="white", font=("Arial", 11), width=12
        ).pack(pady=6)

        self.desenhar_tabuleiro()

    # ══════════════════════════════════════════════════════════
    # DESENHO DO TABULEIRO
    # ══════════════════════════════════════════════════════════
    def desenhar_tabuleiro(self):
        self.canvas.delete("all")
        tabuleiro = (self.estado["tabuleiro"]
                     if self.estado else [[0] * COLUNAS for _ in range(LINHAS)])

        for l in range(LINHAS):
            for c in range(COLUNAS):
                x1 = c * TAMANHO_CASA
                y1 = l * TAMANHO_CASA
                x2 = x1 + TAMANHO_CASA
                y2 = y1 + TAMANHO_CASA

                cor_fundo = (CORES["selecionado"]
                             if self.selecionado == (l, c)
                             else CORES["tabuleiro"])
                self.canvas.create_rectangle(x1, y1, x2, y2,
                                             fill=cor_fundo, outline="#5a4010", width=2)

                valor = tabuleiro[l][c]
                if valor != 0:
                    m = 10
                    self.canvas.create_oval(
                        x1+m, y1+m, x2-m, y2-m,
                        fill=CORES[valor],
                        outline="#000000" if valor == 1 else "#888888",
                        width=2
                    )
                    self.canvas.create_text(
                        (x1+x2)//2, (y1+y2)//2,
                        text=str(valor),
                        fill="#333333" if valor == 1 else "#ffffff",
                        font=("Arial", 14, "bold")
                    )

    # ══════════════════════════════════════════════════════════
    # ATUALIZAÇÃO DA UI
    # ══════════════════════════════════════════════════════════
    def atualizar_interface(self):
        if not self.estado:
            return
        fase          = self.estado["fase"]
        turno         = self.estado["turno"]
        pecas         = self.estado["pecas"]               # peças na mão
        no_tab        = self.estado["pecas_no_tabuleiro"]  # peças no tabuleiro
        deve_capturar = self.estado.get("deve_capturar")

        # Nome da fase exibido ao jogador
        if fase == "posicionamento":
            nome_fase = "Posicionamento"
        elif fase == "captura" and deve_capturar:
            nome_fase = "Captura"
        else:
            nome_fase = "Movimentação"
        self.label_fase.config(text=f"Fase: {nome_fase}")

        # Turno — se deve_capturar for eu, também é "minha vez"
        minha_vez = (turno == self.meu_numero or deve_capturar == self.meu_numero)
        if minha_vez:
            self.label_turno.config(text="Turno: SUA VEZ", fg="green")
        else:
            self.label_turno.config(text=f"Turno: Jogador {turno}", fg="red")

        self.label_pecas.config(
            text=f"Na mão: J1={pecas[0]} | J2={pecas[1]}   "
                 f"No tab: J1={no_tab[0]} | J2={no_tab[1]}")
        self.label_capturas.config(text="")   # capturas calculadas pelas peças no tab

        self.selecionado = None
        self.desenhar_tabuleiro()

    # ══════════════════════════════════════════════════════════
    # CLIQUE NO TABULEIRO
    # ══════════════════════════════════════════════════════════
    def clique_tabuleiro(self, event):
        if self.jogo_encerrado or not self.estado:
            return

        fase          = self.estado["fase"]
        turno         = self.estado["turno"]
        deve_capturar = self.estado.get("deve_capturar")
        tabuleiro     = self.estado["tabuleiro"]

        col   = event.x // TAMANHO_CASA
        linha = event.y // TAMANHO_CASA
        if linha >= LINHAS or col >= COLUNAS:
            return

        # ── Fase de posicionamento ─────────────────────────────
        if fase == "posicionamento":
            if turno != self.meu_numero:
                self.adicionar_chat("[Sistema] Não é sua vez!")
                return
            self.enviar({"tipo": "colocar", "linha": linha, "coluna": col})

        # ── Fase de captura: deve capturar peça do oponente ───
        elif fase == "captura" and deve_capturar == self.meu_numero:
            oponente = 2 if self.meu_numero == 1 else 1
            if tabuleiro[linha][col] == oponente:
                self.enviar({"tipo": "capturar", "linha": linha, "coluna": col})
            else:
                self.adicionar_chat("[Sistema] Clique em uma peça do oponente para capturar.")

        # ── Fase de captura: movimentação normal (dois cliques) ─
        elif fase == "captura" and not deve_capturar:
            if turno != self.meu_numero:
                self.adicionar_chat("[Sistema] Não é sua vez!")
                return
            valor = tabuleiro[linha][col]
            if self.selecionado is None:
                if valor == self.meu_numero:
                    self.selecionado = (linha, col)
                    self.desenhar_tabuleiro()
                    self.adicionar_chat("[Sistema] Peça selecionada. Clique no destino.")
                else:
                    self.adicionar_chat("[Sistema] Selecione uma de suas peças.")
            else:
                de_linha, de_col = self.selecionado
                if (linha, col) == self.selecionado:
                    self.selecionado = None
                    self.desenhar_tabuleiro()
                elif valor == self.meu_numero:
                    self.selecionado = (linha, col)
                    self.desenhar_tabuleiro()
                else:
                    self.enviar({"tipo": "mover",
                                 "de_linha": de_linha, "de_col": de_col,
                                 "para_linha": linha,  "para_col": col})
                    self.selecionado = None

    # ══════════════════════════════════════════════════════════
    # CHAT E DESISTÊNCIA
    # ══════════════════════════════════════════════════════════
    def enviar_chat(self, _event=None):
        texto = self.entrada_chat.get().strip()
        if texto:
            self.enviar({"tipo": "chat", "mensagem": texto})
            self.entrada_chat.delete(0, tk.END)

    def desistir(self):
        if self.jogo_encerrado:
            return
        if messagebox.askyesno("Desistir", "Tem certeza que deseja desistir?"):
            self.enviar({"tipo": "desistir"})

    # ══════════════════════════════════════════════════════════
    # ENVIO
    # ══════════════════════════════════════════════════════════
    def enviar(self, dados):
        try:
            self.sock.sendall((json.dumps(dados) + "\n").encode("utf-8"))
        except Exception as e:
            print(f"Erro ao enviar: {e}")

    def adicionar_chat(self, texto):
        if not hasattr(self, "area_chat"):
            return
        self.area_chat.config(state="normal")
        self.area_chat.insert(tk.END, texto + "\n")
        self.area_chat.see(tk.END)
        self.area_chat.config(state="disabled")


# ─── Ponto de entrada ─────────────────────────────────────────
if __name__ == "__main__":
    ClienteDara()
