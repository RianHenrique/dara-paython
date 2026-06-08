# sensor.py — Interface do Sensor IoT (Produtor de mensagens)
#
# PAPEL NO MOM:
#   Este programa é o PRODUTOR (Publisher).
#   Ele publica mensagens no broker RabbitMQ usando dois tipos de
#   routing key no exchange 'sensores' (tipo topic):
#
#     registro.{tipo}.{id}  → anuncia que este sensor existe
#                              (usado pelo cliente para descobrir sensores)
#
#     dados.{tipo}.{id}     → publica a leitura atual do sensor
#                              (enviado periodicamente e ao atingir limites)

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import queue
import time
import json
import pika
from datetime import datetime

# ══════════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ══════════════════════════════════════════════════════════════

RABBITMQ_HOST  = 'localhost'
EXCHANGE       = 'sensores'       # exchange compartilhado com o cliente
INTERVALO_PUB  = 5                # segundos entre publicações periódicas

UNIDADES = {
    'temperatura': '°C',
    'umidade':     '%',
    'velocidade':  'km/h',
}


# ══════════════════════════════════════════════════════════════
# JANELA DO SENSOR
# ══════════════════════════════════════════════════════════════

class SensorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Sensor IoT — Publicador")
        self.root.resizable(False, False)
        self.root.configure(bg='#f0f0f0')

        # Estado interno
        self._rodando    = False
        self._thread     = None
        self._fila_cmds  = queue.Queue()  # passa comandos da GUI → thread pika

        self._construir_gui()

    # ──────────────────────────────────────────────────────────
    # CONSTRUÇÃO DA INTERFACE
    # ──────────────────────────────────────────────────────────

    def _construir_gui(self):
        PAD = {'padx': 8, 'pady': 4}

        # ── Título ──
        tk.Label(self.root, text="🌡  SENSOR IoT", font=("Arial", 15, "bold"),
                 bg='#2196F3', fg='white').pack(fill='x', ipady=8)

        # ── Frame de configuração ──
        frame_cfg = tk.LabelFrame(self.root, text=" Configuração do Sensor ",
                                  bg='#f0f0f0', font=("Arial", 9, "bold"))
        frame_cfg.pack(fill='x', padx=12, pady=(10, 4))

        # ID
        tk.Label(frame_cfg, text="ID do Sensor:", bg='#f0f0f0').grid(
            row=0, column=0, sticky='w', **PAD)
        self.entry_id = tk.Entry(frame_cfg, width=22)
        self.entry_id.insert(0, "sensor_A")
        self.entry_id.grid(row=0, column=1, **PAD)

        # Tipo
        tk.Label(frame_cfg, text="Tipo:", bg='#f0f0f0').grid(
            row=1, column=0, sticky='w', **PAD)
        self.combo_tipo = ttk.Combobox(frame_cfg, values=list(UNIDADES.keys()),
                                        state='readonly', width=20)
        self.combo_tipo.current(0)
        self.combo_tipo.grid(row=1, column=1, **PAD)

        # Limites
        tk.Label(frame_cfg, text="Limite mínimo:", bg='#f0f0f0').grid(
            row=2, column=0, sticky='w', **PAD)
        self.entry_min = tk.Entry(frame_cfg, width=22)
        self.entry_min.insert(0, "10")
        self.entry_min.grid(row=2, column=1, **PAD)

        tk.Label(frame_cfg, text="Limite máximo:", bg='#f0f0f0').grid(
            row=3, column=0, sticky='w', **PAD)
        self.entry_max = tk.Entry(frame_cfg, width=22)
        self.entry_max.insert(0, "40")
        self.entry_max.grid(row=3, column=1, **PAD)

        self._widgets_cfg = [self.entry_id, self.combo_tipo,
                             self.entry_min, self.entry_max]

        # ── Frame de controle de leitura ──
        frame_ctrl = tk.LabelFrame(self.root, text=" Controle de Leitura ",
                                   bg='#f0f0f0', font=("Arial", 9, "bold"))
        frame_ctrl.pack(fill='x', padx=12, pady=4)

        tk.Label(frame_ctrl, text="Valor atual:", bg='#f0f0f0').grid(
            row=0, column=0, sticky='w', **PAD)

        frame_valor = tk.Frame(frame_ctrl, bg='#f0f0f0')
        frame_valor.grid(row=0, column=1, **PAD)

        self.entry_valor = tk.Entry(frame_valor, width=12, font=("Arial", 11))
        self.entry_valor.insert(0, "25.0")
        self.entry_valor.pack(side='left')

        self.lbl_unidade = tk.Label(frame_valor, text="°C", bg='#f0f0f0',
                                     font=("Arial", 11, "bold"), fg='#555')
        self.lbl_unidade.pack(side='left', padx=4)

        self.btn_alterar = tk.Button(frame_valor, text="Alterar",
                                      command=self._alterar_valor,
                                      state='disabled', bg='#FF9800', fg='white')
        self.btn_alterar.pack(side='left')

        # Atualiza unidade ao trocar tipo
        self.combo_tipo.bind("<<ComboboxSelected>>", self._atualizar_unidade)

        # ── Botão Iniciar / Parar ──
        self.btn_iniciar = tk.Button(
            self.root, text="▶  Iniciar Sensor", width=28,
            bg='#4CAF50', fg='white', font=("Arial", 11, "bold"),
            command=self._toggle_sensor)
        self.btn_iniciar.pack(pady=10)

        # ── Log de publicações ──
        frame_log = tk.LabelFrame(self.root, text=" Log de Publicações ",
                                   bg='#f0f0f0', font=("Arial", 9, "bold"))
        frame_log.pack(fill='both', expand=True, padx=12, pady=(0, 10))

        self.log = scrolledtext.ScrolledText(frame_log, width=50, height=10,
                                              state='disabled', bg='#1e1e1e',
                                              fg='#00ff00', font=("Courier", 9))
        self.log.pack(fill='both', expand=True, padx=4, pady=4)
        self.log.tag_config('alerta', foreground='#ff4444')
        self.log.tag_config('info',   foreground='#aaaaaa')

    # ──────────────────────────────────────────────────────────
    # AÇÕES DA GUI
    # ──────────────────────────────────────────────────────────

    def _atualizar_unidade(self, event=None):
        tipo = self.combo_tipo.get()
        self.lbl_unidade.config(text=UNIDADES.get(tipo, ''))

    def _log(self, msg, tag='normal'):
        """Escreve no log de forma thread-safe (via root.after)."""
        def _write():
            self.log.config(state='normal')
            self.log.insert('end', f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n", tag)
            self.log.see('end')
            self.log.config(state='disabled')
        self.root.after(0, _write)

    def _alterar_valor(self):
        """Lê o campo 'Valor atual' e envia o novo valor para o thread pika."""
        try:
            novo = float(self.entry_valor.get().replace(',', '.'))
            self._fila_cmds.put(('alterar', novo))
        except ValueError:
            messagebox.showerror("Erro", "Digite um número válido no campo de valor.")

    def _toggle_sensor(self):
        if not self._rodando:
            self._iniciar()
        else:
            self._parar()

    def _iniciar(self):
        sensor_id = self.entry_id.get().strip()
        tipo      = self.combo_tipo.get()
        if not sensor_id:
            messagebox.showerror("Erro", "Preencha o ID do sensor.")
            return
        try:
            lim_min = float(self.entry_min.get().replace(',', '.'))
            lim_max = float(self.entry_max.get().replace(',', '.'))
            valor   = float(self.entry_valor.get().replace(',', '.'))
        except ValueError:
            messagebox.showerror("Erro", "Limites e valor devem ser números.")
            return
        if lim_min >= lim_max:
            messagebox.showerror("Erro", "Limite mínimo deve ser menor que o máximo.")
            return

        self._rodando   = True
        self._fila_cmds = queue.Queue()  # fila nova a cada início (evita 'parar' sobrando)
        self.btn_iniciar.config(text="⏹  Parar Sensor", bg='#f44336')
        self.btn_alterar.config(state='normal')
        for w in self._widgets_cfg:
            w.config(state='disabled')

        self._thread = threading.Thread(
            target=self._loop_pika,
            args=(sensor_id, tipo, lim_min, lim_max, valor),
            daemon=True
        )
        self._thread.start()

    def _parar(self):
        self._rodando = False
        self._fila_cmds.put(('parar', None))
        self.btn_iniciar.config(text="▶  Iniciar Sensor", bg='#4CAF50')
        self.btn_alterar.config(state='disabled')
        for w in self._widgets_cfg:
            # Combobox volta pra 'readonly' (não permite digitação livre)
            # Entry volta pra 'normal' (permite editar)
            w.config(state='readonly' if isinstance(w, ttk.Combobox) else 'normal')
        self._log("Sensor parado.", 'info')

    # ──────────────────────────────────────────────────────────
    # THREAD PIKA — comunicação com o RabbitMQ
    # ──────────────────────────────────────────────────────────

    def _loop_pika(self, sensor_id, tipo, lim_min, lim_max, valor_inicial):
        """
        Roda em thread separada para não travar a GUI.

        Responsabilidades:
          1. Conectar ao RabbitMQ e declarar o exchange
          2. Registrar o sensor periodicamente (para o cliente descobrir)
          3. Publicar leituras periodicamente
          4. Publicar ALERTA imediato quando o valor ultrapassa os limites
          5. Processar comandos da GUI (alterar valor / parar)
        """
        # ── Conectar ──
        try:
            conn = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST))
            ch = conn.channel()
            # Declara exchange tipo 'topic': roteamento por padrões de chave
            ch.exchange_declare(exchange=EXCHANGE, exchange_type='topic', durable=True)
        except Exception as e:
            self._log(f"ERRO ao conectar: {e}", 'alerta')
            self._rodando = False
            return

        unidade     = UNIDADES[tipo]
        valor_atual = valor_inicial
        ultimo_pub  = 0

        # ── Funções auxiliares ──
        def publicar_dados(valor, motivo="periódico"):
            em_alerta = valor < lim_min or valor > lim_max
            payload = json.dumps({
                "id":        sensor_id,
                "tipo":      tipo,
                "valor":     valor,
                "unidade":   unidade,
                "lim_min":   lim_min,
                "lim_max":   lim_max,
                "alerta":    em_alerta,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
            })
            # Routing key de dados: usado pelo cliente para receber leituras
            ch.basic_publish(
                exchange=EXCHANGE,
                routing_key=f"dados.{tipo}.{sensor_id}",
                body=payload
            )
            status = "⚠ ALERTA" if em_alerta else "OK"
            tag    = 'alerta' if em_alerta else 'normal'
            self._log(f"{valor}{unidade}  [{status}]  ({motivo})", tag)

        def publicar_registro():
            # Routing key de registro: o cliente escuta 'registro.#' para
            # descobrir automaticamente quais sensores estão ativos
            payload = json.dumps({
                "id":      sensor_id,
                "tipo":    tipo,
                "unidade": unidade,
            })
            ch.basic_publish(
                exchange=EXCHANGE,
                routing_key=f"registro.{tipo}.{sensor_id}",
                body=payload
            )

        self._log(f"Conectado! Publicando em 'dados.{tipo}.{sensor_id}'")
        publicar_registro()
        publicar_dados(valor_atual, "inicial")
        ultimo_pub = time.time()

        # ── Loop principal ──
        while self._rodando:
            # Processa eventos de rede do pika (mantém conexão viva)
            conn.process_data_events(time_limit=0.1)

            # Verifica se há comandos da GUI
            try:
                cmd, arg = self._fila_cmds.get_nowait()
                if cmd == 'alterar':
                    valor_atual = arg
                    publicar_dados(valor_atual, "manual")
                    ultimo_pub = time.time()
                elif cmd == 'parar':
                    break
            except queue.Empty:
                pass

            # Publicação periódica
            if time.time() - ultimo_pub >= INTERVALO_PUB:
                publicar_dados(valor_atual, "periódico")
                publicar_registro()   # mantém o sensor visível para os clientes
                ultimo_pub = time.time()

        conn.close()
        self._log("Desconectado do RabbitMQ.", 'info')


# ══════════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    root = tk.Tk()            # cria a janela principal
    app  = SensorApp(root)    # constrói a interface dentro da janela
    root.mainloop()           # loop infinito: mantém a janela aberta e responsiva
