# jogo.py — Lógica pura do jogo Dara (regras, tabuleiro, condições de vitória)
# Não sabe nada sobre sockets ou rede, só as regras do jogo.

LINHAS         = 5
COLUNAS        = 6
PECAS_INICIAIS = 12


# ══════════════════════════════════════════════════════════════
# DETECÇÃO DE LINHAS
# ══════════════════════════════════════════════════════════════

def obter_linhas(tabuleiro, jogador):
    """
    Retorna um set com identificadores únicos de todas as linhas de 3
    peças consecutivas do jogador no tabuleiro.

    Identificador:
      "h-L-C" = trio horizontal na linha L, começando na coluna C
      "v-L-C" = trio vertical  começando na linha L, coluna C

    Usar identificadores (em vez de True/False) permite comparar os
    sets antes e depois de um movimento, detectando apenas trios NOVOS
    — evitando o bug de trios antigos serem contados de novo.
    """
    linhas = set()

    # Horizontais: janela deslizante de 3 colunas
    for l in range(LINHAS):
        for c in range(COLUNAS - 2):
            if (tabuleiro[l][c]   == jogador and
                tabuleiro[l][c+1] == jogador and
                tabuleiro[l][c+2] == jogador):
                linhas.add(f"h-{l}-{c}")

    # Verticais: janela deslizante de 3 linhas
    for l in range(LINHAS - 2):
        for c in range(COLUNAS):
            if (tabuleiro[l][c]   == jogador and
                tabuleiro[l+1][c] == jogador and
                tabuleiro[l+2][c] == jogador):
                linhas.add(f"v-{l}-{c}")

    return linhas


def formaria_linha(tabuleiro, jogador, linha, coluna):
    """
    Retorna True se colocar a peça do jogador em (linha, coluna)
    formaria algum trio de 3 em linha.

    Usado para barrar jogadas na fase de posicionamento.
    Faz uma cópia do tabuleiro para não alterar o estado real.
    """
    temp = [row[:] for row in tabuleiro]  # cópia rasa do tabuleiro
    temp[linha][coluna] = jogador
    return len(obter_linhas(temp, jogador)) > 0


# ══════════════════════════════════════════════════════════════
# ESTADO INICIAL
# ══════════════════════════════════════════════════════════════

def criar_estado():
    """
    Cria e retorna o estado inicial de uma partida zerada.
    Espelho da função criarEstadoInicial() do jogo.js.

    Campos:
      tabuleiro          — grade 5x6, 0=vazio, 1=jogador1, 2=jogador2
      turno              — quem joga agora (1 ou 2)
      fase               — etapa atual da partida:
                             "posicionamento" → jogadores colocam as peças
                             "captura"        → jogadores movem e capturam
                             "encerrado"      → partida finalizada
      pecas              — peças ainda na mão de cada jogador [J1, J2]
      pecas_no_tabuleiro — peças de cada jogador no tabuleiro  [J1, J2]
      vencedor           — None enquanto o jogo está em curso, 1 ou 2 ao fim
      deve_capturar      — None no estado normal; igual ao número do jogador
                           que formou um trio e precisa capturar uma peça do
                           oponente antes de qualquer outra ação
    """
    return {
        "tabuleiro":          [[0] * COLUNAS for _ in range(LINHAS)],
        "turno":              1,
        "fase":               "posicionamento",
        "pecas":              [PECAS_INICIAIS, PECAS_INICIAIS],
        "pecas_no_tabuleiro": [0, 0],
        "vencedor":           None,
        "deve_capturar":      None,
    }
