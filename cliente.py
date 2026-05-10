# cliente.py — Cliente RPC com Pyro5
# Interface gráfica Tkinter + polling para receber atualizações do servidor.

import time
import threading
import Pyro5.api
import tkinter as tk
from tkinter import messagebox, scrolledtext

# ─── Constantes ───────────────────────────────────────────────
LINHAS        = 5
COLUNAS       = 6
PORTA         = 9090
TAMANHO_CASA  = 80
POLL_INTERVAL = 0.3   # segundos entre cada chamada get_estado()

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
        self.proxy        = None
        self.proxy_lock   = threading.Lock()
        self.meu_numero   = None
        self.estado       = None
        self.selecionado  = None
        self._polling     = False
        self.jogo_encerrado   = False
        self.ultimo_chat_idx  = 0
        self.ultimo_msg_seq   = -1
        self._jogo_iniciado   = False
        self._ultimo_ip       = "127.0.0.1"
        self._btn_nova        = None

        self.janela = tk.Tk()
        self.janela.title("Jogo Dara")
        self.janela.resizable(False, False)
        self.janela.protocol("WM_DELETE_WINDOW", self._fechar_janela)

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

    def conectar(self):
        ip = self.entrada_ip.get().strip()
        self._ultimo_ip = ip
        try:
            uri         = f"PYRO:dara.servidor@{ip}:{PORTA}"
            self.proxy  = Pyro5.api.Proxy(uri)
            resultado   = self._rpc(lambda p: p.entrar())

            if not resultado["ok"]:
                self.label_status.config(text=f"Erro: {resultado['erro']}", fg="red")
                return

            self.meu_numero = resultado["jogador"]
            self.janela.title(f"Jogo Dara — Você é o Jogador {self.meu_numero}")

            if self.meu_numero == 1:
                self.label_status.config(text="Aguardando o segundo jogador...", fg="orange")
            else:
                self.label_status.config(text="Conectado! Iniciando partida...", fg="green")

            self._polling = True
            threading.Thread(target=self._loop_polling, daemon=True).start()

        except Exception as e:
            self.label_status.config(text=f"Erro ao conectar: {e}", fg="red")

    # ══════════════════════════════════════════════════════════
    # POLLING (thread separada)
    # ══════════════════════════════════════════════════════════

    def _loop_polling(self):
        """
        Fica chamando get_estado() a cada POLL_INTERVAL segundos.
        Diferente dos sockets (onde o servidor empurrava os dados),
        aqui o cliente puxa — esse padrão se chama polling.
        Usa proxy próprio porque proxies Pyro5 não são thread-safe.
        """
        uri        = f"PYRO:dara.servidor@{self._ultimo_ip}:{PORTA}"
        poll_proxy = Pyro5.api.Proxy(uri)
        falhas     = 0
        while self._polling:
            try:
                idx    = self.ultimo_chat_idx
                estado = poll_proxy.get_estado(idx)
                falhas = 0
                self.janela.after(0, self._processar_estado, estado)
            except Exception as e:
                print(f"[polling] {e}")
                falhas += 1
                if falhas >= 3:
                    self.janela.after(0, self._servidor_desconectou)
                    break
            time.sleep(POLL_INTERVAL)

    def _fechar_janela(self):
        """Intercepta o X da janela: desiste da partida antes de fechar."""
        if self._jogo_iniciado and not self.jogo_encerrado and self.proxy:
            try:
                self._rpc(lambda p: p.desistir(self.meu_numero))
            except Exception:
                pass
        self._polling = False
        self.janela.destroy()

    def _servidor_desconectou(self):
        """Chamado quando o polling falha 3 vezes seguidas."""
        if not self.jogo_encerrado:
            self.jogo_encerrado = True
            self._polling       = False
            messagebox.showerror("Conexão perdida", "O servidor desconectou!")
            if self._jogo_iniciado:
                self._mostrar_botao_nova_partida()

    def _processar_estado(self, estado):
        """Processa o estado recebido — sempre executado na thread principal via after()."""

        # Novas mensagens de chat
        novas = estado.get("novas_msgs", [])
        for msg in novas:
            self.adicionar_chat(f"Jogador {msg['jogador']}: {msg['mensagem']}")
        self.ultimo_chat_idx += len(novas)

        # Transição: tela de espera → interface do jogo
        if not self._jogo_iniciado and estado["jogadores_conectados"] == 2:
            self._jogo_iniciado = True
            try:
                self.frame_conexao.destroy()
            except Exception:
                pass
            self.construir_interface_jogo()

        if not self._jogo_iniciado:
            return

        self.estado = estado

        if hasattr(self, "label_fase"):
            self.atualizar_interface()

        # Mensagem de sistema (só exibe quando é nova, pelo msg_seq)
        msg_seq = estado.get("msg_seq", 0)
        if msg_seq > self.ultimo_msg_seq and estado.get("mensagem_sistema"):
            self.ultimo_msg_seq = msg_seq
            self.adicionar_chat(f"[Sistema] {estado['mensagem_sistema']}")

        # Fim de jogo
        if estado["vencedor"] and not self.jogo_encerrado:
            self.jogo_encerrado = True
            self._polling       = False
            msg = estado.get("mensagem_sistema", "")
            if estado["vencedor"] == self.meu_numero:
                messagebox.showinfo("FIM DE JOGO", f"Você VENCEU!\n{msg}")
            else:
                messagebox.showinfo("FIM DE JOGO", f"Você perdeu.\n{msg}")
            self._mostrar_botao_nova_partida()

    # ══════════════════════════════════════════════════════════
    # NOVA PARTIDA
    # ══════════════════════════════════════════════════════════

    def _mostrar_botao_nova_partida(self):
        if not self._btn_nova:
            self._btn_nova = tk.Button(
                self.janela,
                text="Nova Partida",
                command=self._nova_partida,
                bg="#2196F3", fg="white",
                font=("Arial", 12, "bold"), width=14
            )
            self._btn_nova.pack(pady=8)

    def _nova_partida(self):
        ip = self._ultimo_ip

        self.proxy            = None
        self.meu_numero       = None
        self.estado           = None
        self.selecionado      = None
        self._polling         = False
        self.jogo_encerrado   = False
        self.ultimo_chat_idx  = 0
        self.ultimo_msg_seq   = -1
        self._jogo_iniciado   = False
        self._btn_nova        = None

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

        frame_info = tk.Frame(frame, bd=2, relief="groove", pady=4)
        frame_info.pack(fill="x", pady=(0, 6))

        self.label_fase = tk.Label(frame_info, text="Fase: Posicionamento",
                                   font=("Arial", 11, "bold"))
        self.label_fase.pack(side="left", padx=12)

        self.label_turno = tk.Label(frame_info, text="Turno: —",
                                    font=("Arial", 11))
        self.label_turno.pack(side="left", padx=12)

        self.label_pecas = tk.Label(frame_info, text="Na mão: J1=12 | J2=12",
                                    font=("Arial", 11))
        self.label_pecas.pack(side="left", padx=12)

        self.label_capturas = tk.Label(frame_info, text="",
                                       font=("Arial", 11))
        self.label_capturas.pack(side="left", padx=12)

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
    # TABULEIRO
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

                cor = (CORES["selecionado"] if self.selecionado == (l, c)
                       else CORES["tabuleiro"])
                self.canvas.create_rectangle(x1, y1, x2, y2,
                                             fill=cor, outline="#5a4010", width=2)

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
        pecas         = self.estado["pecas"]
        no_tab        = self.estado["pecas_no_tabuleiro"]
        deve_capturar = self.estado.get("deve_capturar")

        if fase == "posicionamento":
            nome_fase = "Posicionamento"
        elif fase == "captura" and deve_capturar:
            nome_fase = "Captura"
        else:
            nome_fase = "Movimentação"
        self.label_fase.config(text=f"Fase: {nome_fase}")

        minha_vez = (turno == self.meu_numero or deve_capturar == self.meu_numero)
        if minha_vez:
            self.label_turno.config(text="Turno: SUA VEZ", fg="green")
        else:
            self.label_turno.config(text=f"Turno: Jogador {turno}", fg="red")

        self.label_pecas.config(
            text=f"Na mão: J1={pecas[0]} | J2={pecas[1]}   "
                 f"No tab: J1={no_tab[0]} | J2={no_tab[1]}")
        self.label_capturas.config(text="")

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
            res = self._rpc(lambda p: p.colocar(self.meu_numero, linha, col))
            if res["ok"]:
                self._atualizar_agora()
            else:
                self.adicionar_chat(f"[Erro] {res['erro']}")

        # ── Capturar peça do oponente ──────────────────────────
        elif fase == "captura" and deve_capturar == self.meu_numero:
            oponente = 2 if self.meu_numero == 1 else 1
            if tabuleiro[linha][col] == oponente:
                res = self._rpc(lambda p: p.capturar(self.meu_numero, linha, col))
                if res["ok"]:
                    self._atualizar_agora()
                else:
                    self.adicionar_chat(f"[Erro] {res['erro']}")
            else:
                self.adicionar_chat("[Sistema] Clique em uma peça do oponente para capturar.")

        # ── Movimentação normal (dois cliques) ─────────────────
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
                    res = self._rpc(
                        lambda p: p.mover(self.meu_numero, de_linha, de_col, linha, col))
                    self.selecionado = None
                    if res["ok"]:
                        self._atualizar_agora()
                    else:
                        self.adicionar_chat(f"[Erro] {res['erro']}")

    # ══════════════════════════════════════════════════════════
    # CHAT E DESISTÊNCIA
    # ══════════════════════════════════════════════════════════

    def enviar_chat(self, _event=None):
        texto = self.entrada_chat.get().strip()
        if texto:
            self._rpc(lambda p: p.chat(self.meu_numero, texto))
            self.entrada_chat.delete(0, tk.END)

    def desistir(self):
        if self.jogo_encerrado:
            return
        if messagebox.askyesno("Desistir", "Tem certeza que deseja desistir?"):
            self._rpc(lambda p: p.desistir(self.meu_numero))

    # ══════════════════════════════════════════════════════════
    # RPC — executa chamada remota com lock para thread safety
    # ══════════════════════════════════════════════════════════

    def _atualizar_agora(self):
        """Busca o estado imediatamente após uma ação, sem esperar o próximo poll."""
        try:
            idx    = self.ultimo_chat_idx
            estado = self._rpc(lambda p: p.get_estado(idx))
            self._processar_estado(estado)
        except Exception:
            pass

    def _rpc(self, func):
        with self.proxy_lock:
            return func(self.proxy)

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
