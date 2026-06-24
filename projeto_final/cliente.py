# cliente.py — Cliente de mensagens com suporte online/offline
#
# FLUXO:
#   1. Login: conecta ao servidor via Pyro5, registra e entra como online
#   2. Thread de polling (a cada 2s): busca mensagens novas no servidor
#   3. Thread de contatos (a cada 3s): atualiza status online/offline da lista
#   4. Envio: chama servidor.enviar() → servidor decide se entrega direto ou guarda offline
#   5. Toggle: logout() → fica offline; login() → volta online e recebe msgs da fila

import tkinter as tk
from tkinter import scrolledtext, messagebox, simpledialog
import threading
import time
import Pyro5.api
from datetime import datetime

PORTA          = 9091
POLL_INTERVALO = 2   # segundos entre cada busca de mensagens


# ══════════════════════════════════════════════════════════════
# APLICAÇÃO DO CLIENTE
# ══════════════════════════════════════════════════════════════

class ClienteApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Mensageiro")
        self.root.configure(bg='#f0f0f0')

        self.nome           = None
        self._ip            = 'localhost'
        self.online         = False
        self.proxy          = None          # proxy Pyro5 principal (thread da GUI)
        self._proxy_lock    = threading.Lock()
        self.contato_ativo  = None          # contato selecionado no chat
        self.historico      = {}            # {contato: [(de, msg, ts, offline), ...]}
        self._polling       = False

        self._tela_login()

    def _rpc(self, func):
        """Executa chamada RPC com lock — evita chamadas simultâneas no mesmo proxy."""
        with self._proxy_lock:
            return func(self.proxy)

    # ──────────────────────────────────────────────────────────
    # TELA DE LOGIN
    # ──────────────────────────────────────────────────────────

    def _tela_login(self):
        self.root.geometry("360x220")
        self.root.resizable(False, False)

        frame = tk.Frame(self.root, bg='#f0f0f0', padx=25, pady=20)
        frame.pack(expand=True, fill='both')

        tk.Label(frame, text="💬 Mensageiro",
                 font=("Arial", 17, "bold"), bg='#f0f0f0', fg='#1976D2').pack(pady=(0, 15))

        tk.Label(frame, text="Servidor (IP):", bg='#f0f0f0').pack(anchor='w')
        self.entry_ip = tk.Entry(frame, width=35)
        self.entry_ip.insert(0, "localhost")
        self.entry_ip.pack(pady=(0, 6))

        tk.Label(frame, text="Seu nome de contato:", bg='#f0f0f0').pack(anchor='w')
        self.entry_nome = tk.Entry(frame, width=35)
        self.entry_nome.pack(pady=(0, 10))
        self.entry_nome.bind('<Return>', lambda e: self._conectar())

        tk.Button(frame, text="Entrar", command=self._conectar,
                  bg='#1976D2', fg='white', font=("Arial", 10, "bold"),
                  width=22, relief='flat').pack()

    def _conectar(self):
        nome = self.entry_nome.get().strip()
        ip   = self.entry_ip.get().strip() or 'localhost'
        if not nome:
            messagebox.showerror("Erro", "Digite seu nome de contato.")
            return

        # Testa conexão com o servidor
        try:
            uri   = f"PYRO:mensageiro.servidor@{ip}:{PORTA}"
            proxy = Pyro5.api.Proxy(uri)
            proxy.listar_clientes()  # chamada simples para verificar se o servidor responde
            self.proxy = proxy
            self.nome  = nome
            self._ip   = ip          # salva o IP antes de destruir os widgets
        except Exception as e:
            messagebox.showerror("Erro de conexão", f"Não foi possível conectar:\n{e}")
            return

        # Registra (cria fila RabbitMQ se for novo) e faz login
        self._rpc(lambda p: p.registrar(nome))
        self._rpc(lambda p: p.login(nome))
        self.online = True

        # Destrói tela de login e constrói GUI principal
        for w in self.root.winfo_children():
            w.destroy()
        self.root.resizable(True, True)
        self.root.geometry("760x530")
        self._construir_gui()
        self._iniciar_polling()
        # Intercepta o fechamento da janela para fazer logout antes de sair
        self.root.protocol("WM_DELETE_WINDOW", self._fechar_janela)

    # ──────────────────────────────────────────────────────────
    # GUI PRINCIPAL
    # ──────────────────────────────────────────────────────────

    def _construir_gui(self):
        self.root.title(f"Mensageiro — {self.nome}")

        # ── Barra superior ──
        barra = tk.Frame(self.root, bg='#1565C0', pady=7)
        barra.pack(fill='x')

        tk.Label(barra, text=f"  👤 {self.nome}",
                 font=("Arial", 11, "bold"), bg='#1565C0', fg='white').pack(side='left')

        self.lbl_status = tk.Label(barra, text="● ONLINE",
                                    font=("Arial", 10, "bold"),
                                    bg='#1565C0', fg='#A5D6A7')
        self.lbl_status.pack(side='left', padx=10)

        self.btn_toggle = tk.Button(barra, text="Ficar Offline",
                                     command=self._toggle_status,
                                     bg='#E53935', fg='white',
                                     font=("Arial", 9, "bold"), relief='flat', padx=10)
        self.btn_toggle.pack(side='right', padx=10)

        # ── Frame principal: 2 colunas ──
        frame_main = tk.Frame(self.root, bg='#f0f0f0')
        frame_main.pack(fill='both', expand=True, padx=8, pady=6)

        # ── Coluna esquerda: lista de contatos ──
        frame_esq = tk.LabelFrame(frame_main, text=" Contatos ",
                                   bg='#f0f0f0', font=("Arial", 9, "bold"), width=200)
        frame_esq.pack(side='left', fill='y', padx=(0, 6))
        frame_esq.pack_propagate(False)

        self.lista_contatos = tk.Listbox(
            frame_esq, width=22, font=("Courier", 10),
            selectbackground='#1565C0', selectforeground='white',
            activestyle='none')
        self.lista_contatos.pack(fill='both', expand=True, padx=4, pady=(4, 0))
        self.lista_contatos.bind('<<ListboxSelect>>', self._selecionar_contato)

        frame_btns = tk.Frame(frame_esq, bg='#f0f0f0')
        frame_btns.pack(fill='x', padx=4, pady=4)
        tk.Button(frame_btns, text="+ Adicionar",
                  command=self._adicionar_contato,
                  bg='#2E7D32', fg='white', font=("Arial", 8, "bold"), relief='flat'
                  ).pack(side='left', expand=True, fill='x', padx=(0, 2))
        tk.Button(frame_btns, text="− Remover",
                  command=self._remover_contato,
                  bg='#B71C1C', fg='white', font=("Arial", 8, "bold"), relief='flat'
                  ).pack(side='left', expand=True, fill='x')

        # ── Coluna direita: área de chat ──
        frame_dir = tk.Frame(frame_main, bg='#f0f0f0')
        frame_dir.pack(side='left', fill='both', expand=True)

        self.lbl_chat = tk.Label(frame_dir,
                                  text="  Selecione um contato para conversar",
                                  font=("Arial", 10, "bold"),
                                  bg='#E3F2FD', fg='#1565C0', anchor='w', pady=5)
        self.lbl_chat.pack(fill='x')

        self.area_chat = scrolledtext.ScrolledText(
            frame_dir, state='disabled',
            font=("Arial", 10), bg='#FAFAFA')
        self.area_chat.pack(fill='both', expand=True, pady=(4, 0))

        # Tags de cores para os diferentes tipos de mensagem
        self.area_chat.tag_config('eu',      foreground='#1565C0', font=("Arial", 10, "bold"))
        self.area_chat.tag_config('outro',   foreground='#212121')
        self.area_chat.tag_config('offline', foreground='#E65100')  # msgs que vieram da fila
        self.area_chat.tag_config('sistema', foreground='#9E9E9E', font=("Arial", 8, "italic"))

        frame_envio = tk.Frame(frame_dir, bg='#f0f0f0')
        frame_envio.pack(fill='x', pady=4)

        self.entry_msg = tk.Entry(frame_envio, font=("Arial", 10))
        self.entry_msg.pack(side='left', fill='x', expand=True, padx=(0, 4))
        self.entry_msg.bind('<Return>', lambda e: self._enviar())

        tk.Button(frame_envio, text="Enviar  →",
                  command=self._enviar,
                  bg='#1565C0', fg='white', font=("Arial", 10, "bold"),
                  relief='flat', padx=10).pack(side='left')

    # ──────────────────────────────────────────────────────────
    # AÇÕES DA GUI
    # ──────────────────────────────────────────────────────────

    def _fechar_janela(self):
        """Faz logout no servidor antes de fechar a janela."""
        try:
            self._rpc(lambda p: p.logout(self.nome))
        except Exception:
            pass
        self._polling = False
        self.root.destroy()

    def _toggle_status(self):
        """Alterna entre online e offline."""
        if self.online:
            self._rpc(lambda p: p.logout(self.nome))
            self.online = False
            self.lbl_status.config(text="○ OFFLINE", fg='#EF9A9A')
            self.btn_toggle.config(text="Ficar Online", bg='#2E7D32')
        else:
            self._rpc(lambda p: p.login(self.nome))
            self.online = True
            self.lbl_status.config(text="● ONLINE", fg='#A5D6A7')
            self.btn_toggle.config(text="Ficar Offline", bg='#E53935')

    def _selecionar_contato(self, event=None):
        """Usuário clicou em um contato da lista."""
        sel = self.lista_contatos.curselection()
        if not sel:
            return
        texto = self.lista_contatos.get(sel[0])
        # Formato: "● Alice" ou "○ Alice" → extrai "Alice"
        nome = texto[2:].strip()
        self.contato_ativo = nome
        self.lbl_chat.config(text=f"  Chat com: {nome}")
        if nome not in self.historico:
            self.historico[nome] = []
        self._renderizar_chat(nome)

    def _renderizar_chat(self, contato):
        """Re-desenha toda a área de chat com o histórico do contato."""
        self.area_chat.config(state='normal')
        self.area_chat.delete('1.0', 'end')
        for de, msg, ts, offline in self.historico.get(contato, []):
            if de == self.nome:
                self.area_chat.insert('end', f"[{ts}] Eu: {msg}\n", 'eu')
            else:
                tag    = 'offline' if offline else 'outro'
                label  = "(offline) " if offline else ""
                self.area_chat.insert('end', f"[{ts}] {de}: {label}{msg}\n", tag)
        self.area_chat.see('end')
        self.area_chat.config(state='disabled')

    def _adicionar_msg_historico(self, de, msg, ts, offline=False):
        """Adiciona mensagem ao histórico e atualiza a tela se for o chat ativo."""
        # Determina a qual conversa a mensagem pertence
        contato = de if de != self.nome else self.contato_ativo
        if contato is None:
            return
        if contato not in self.historico:
            self.historico[contato] = []
        self.historico[contato].append((de, msg, ts, offline))
        if self.contato_ativo == contato:
            self._renderizar_chat(contato)

    def _enviar(self):
        """Envia mensagem para o contato ativo via RPC."""
        if not self.contato_ativo:
            messagebox.showinfo("Aviso", "Selecione um contato para enviar.")
            return
        msg = self.entry_msg.get().strip()
        if not msg:
            return
        self.entry_msg.delete(0, 'end')
        ts = datetime.now().strftime('%H:%M:%S')

        try:
            resultado = self._rpc(lambda p: p.enviar(self.nome, self.contato_ativo, msg))
            if resultado.get('ok'):
                self._adicionar_msg_historico(self.nome, msg, ts)
                # Avisa na tela se o contato estava offline
                if resultado.get('entregue') == 'offline':
                    self.area_chat.config(state='normal')
                    self.area_chat.insert('end',
                        f"  ↳ {self.contato_ativo} está offline — mensagem guardada na fila\n",
                        'sistema')
                    self.area_chat.see('end')
                    self.area_chat.config(state='disabled')
            else:
                messagebox.showerror("Erro", resultado.get('erro', 'Falha ao enviar'))
        except Exception as e:
            messagebox.showerror("Erro", str(e))

    def _adicionar_contato(self):
        """Abre diálogo para adicionar um novo contato."""
        contato = simpledialog.askstring("Adicionar contato", "Nome do contato:")
        if not contato or not contato.strip():
            return
        resultado = self._rpc(lambda p: p.adicionar_contato(self.nome, contato.strip()))
        if resultado.get('ok'):
            self._atualizar_contatos()
        else:
            messagebox.showerror("Erro", resultado.get('erro', 'Erro desconhecido'))

    def _remover_contato(self):
        """Remove o contato atualmente selecionado."""
        if not self.contato_ativo:
            messagebox.showinfo("Aviso", "Selecione um contato na lista para remover.")
            return
        if not messagebox.askyesno("Remover", f"Remover '{self.contato_ativo}' dos contatos?"):
            return
        self._rpc(lambda p: p.remover_contato(self.nome, self.contato_ativo))
        self.contato_ativo = None
        self.lbl_chat.config(text="  Selecione um contato para conversar")
        self.area_chat.config(state='normal')
        self.area_chat.delete('1.0', 'end')
        self.area_chat.config(state='disabled')
        self._atualizar_contatos()

    def _atualizar_contatos(self):
        """Atualiza a lista de contatos com status online/offline atual."""
        try:
            contatos    = self._rpc(lambda p: p.listar_contatos(self.nome))
            sel_atual   = self.contato_ativo
            self.lista_contatos.delete(0, 'end')
            for i, c in enumerate(contatos):
                icone = '●' if c['online'] else '○'
                self.lista_contatos.insert('end', f"{icone} {c['nome']}")
                if c['nome'] == sel_atual:
                    self.lista_contatos.selection_set(i)
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────
    # POLLING — busca mensagens novas periodicamente
    # ──────────────────────────────────────────────────────────

    def _iniciar_polling(self):
        self._polling = True
        threading.Thread(target=self._loop_polling, daemon=True).start()

    def _loop_polling(self):
        """
        Roda em thread separada. A cada POLL_INTERVALO segundos:
        - Busca mensagens novas no servidor (online e offline recém-entregues)
        - A cada 3s, atualiza o status dos contatos
        """
        uri         = f"PYRO:mensageiro.servidor@{self._ip}:{PORTA}"
        poll_proxy  = Pyro5.api.Proxy(uri)  # proxy exclusivo desta thread
        ultimo_cont = 0

        while self._polling:
            try:
                # Busca mensagens pendentes (entrega instantânea ou msgs offline liberadas)
                msgs = poll_proxy.buscar_mensagens(self.nome)
                for m in msgs:
                    self.root.after(0, self._adicionar_msg_historico,
                                    m['de'], m['msg'], m['timestamp'],
                                    m.get('offline', False))

                # Atualiza lista de contatos a cada 3 segundos
                agora = time.time()
                if agora - ultimo_cont >= 3:
                    self.root.after(0, self._atualizar_contatos)
                    ultimo_cont = agora

            except Exception:
                pass
            time.sleep(POLL_INTERVALO)


# ══════════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    root = tk.Tk()
    app  = ClienteApp(root)
    root.mainloop()
