# Many VRCX: gera um arquivo só com as instâncias do grupo Many, para enviar na aba Métricas do Many Planner.
# Lê o banco do VRCX sem alterar nada. Mesma conta de vrcx/metricas.mjs (noite de 10h às 10h, horário de Brasília).
# Uso: clique duas vezes no .exe, marque as festas e exporte. Para teste: extrator.py --banco VRCX.sqlite3 --saida arquivo.json
import ctypes
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

VERSAO = '1.0.2'
REPO = 'manyml/many-vrcx'
GRUPO_MANY = 'grp_8e19683f-b1c4-4a07-8bfe-f29c08670e24'
PASSO_MIN = 15  # gráfico de lotação: um ponto a cada 15 min
MAX_PESSOAS = 400  # limite por festa
HORA = 3600000


def ms(texto):
    t = datetime.fromisoformat(texto.replace('Z', '+00:00'))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return int(round(t.timestamp() * 1000))


def iso(t):
    d = datetime.fromtimestamp(t // 1000, timezone.utc)
    return d.strftime('%Y-%m-%dT%H:%M:%S.') + f'{t % 1000:03d}Z'


def noite_de(t):
    # 22h às 02h conta como a noite em que começou (corte às 10h da manhã, horário de Brasília)
    return datetime.fromtimestamp((t - 13 * HORA) / 1000, timezone.utc).strftime('%Y-%m-%d')


def arredonda(x):
    return math.floor(x + 0.5)


def medir_noite(sessoes, eventos):
    eventos = sorted(eventos, key=lambda e: (ms(e['created_at']), e['id']))
    inicio = min(ms(s['created_at']) for s in sessoes)
    fim = max([ms(s['created_at']) + int(s['time'] or 0) for s in sessoes] + [ms(e['created_at']) for e in eventos])
    abertos, pessoas, mudancas = {}, {}, []
    agora = pico = 0
    pico_em = inicio
    for e in eventos:
        quem = e['user_id'] or e['display_name']
        chave = (e['location'], quem)
        t = ms(e['created_at'])
        p = pessoas.setdefault(quem, {'nome': e['display_name'], 'userId': e['user_id'] or '', 'ms': 0})
        p['nome'] = e['display_name']  # fica o nome mais recente
        if e['type'] == 'OnPlayerJoined':
            if chave in abertos:  # ao reentrar, o VRCX registra de novo quem já estava
                continue
            abertos[chave] = (quem, t)
            agora += 1
            if agora > pico:
                pico, pico_em = agora, t
        elif e['type'] == 'OnPlayerLeft' and chave in abertos:
            p['ms'] += t - abertos.pop(chave)[1]
            agora -= 1
        else:
            continue
        mudancas.append((t, agora))
    for quem, t in abertos.values():
        pessoas[quem]['ms'] += fim - t
    serie, i, n, t = [], 0, 0, inicio
    while t <= fim:
        while i < len(mudancas) and mudancas[i][0] <= t:
            n = mudancas[i][1]
            i += 1
        serie.append({'t': iso(t), 'n': n})
        t += PASSO_MIN * 60000
    lista = [{'nome': p['nome'], 'userId': p['userId'], 'minutos': arredonda(p['ms'] / 60000)} for p in pessoas.values()]
    com_tempo = [p['minutos'] for p in lista if p['minutos'] > 0]
    return {
        'inicio': iso(inicio), 'fim': iso(fim), 'unicos': len(lista), 'pico': pico, 'picoEm': iso(pico_em),
        'tempoMedioMin': arredonda(sum(com_tempo) / len(com_tempo)) if com_tempo else 0, 'serie': serie,
        'pessoas': sorted(lista, key=lambda p: -p['minutos'])[:MAX_PESSOAS],
    }


def abrir_banco(caminho):
    # só leitura: o arquivo do VRCX não é alterado. Se ele estiver travado, extrair() lê de uma cópia temporária
    uri = Path(caminho).resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('select 1 from gamelog_location limit 1').fetchall()
    return db


def extrair(caminho):
    filtro = f'%group({GRUPO_MANY})%'
    temp = None
    try:
        try:
            db = abrir_banco(caminho)
        except sqlite3.OperationalError as e:
            if 'no such table' in str(e):
                raise
            temp = tempfile.mkdtemp(prefix='many-vrcx-')
            for fim in ('', '-wal', '-shm'):
                if os.path.exists(str(caminho) + fim):
                    shutil.copy2(str(caminho) + fim, os.path.join(temp, 'VRCX.sqlite3' + fim))
            db = abrir_banco(os.path.join(temp, 'VRCX.sqlite3'))
        locs = [dict(r) for r in db.execute(
            'select location, min(created_at) primeiro, max(world_name) mundo, max(world_id) worldId '
            'from gamelog_location where location like ? group by location order by primeiro', (filtro,))]
        sessoes, eventos = {}, {}
        for r in db.execute('select location, created_at, time from gamelog_location where location like ?', (filtro,)):
            sessoes.setdefault(r['location'], []).append(dict(r))
        for r in db.execute('select id, location, created_at, type, display_name, user_id from gamelog_join_leave where location like ?', (filtro,)):
            eventos.setdefault(r['location'], []).append(dict(r))
        db.close()
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)
    por_noite = {}
    for l in locs:
        por_noite.setdefault(noite_de(ms(l['primeiro'])), []).append(l)
    noites = []
    for noite, ls in sorted(por_noite.items()):
        evs = [e for l in ls for e in eventos.get(l['location'], [])]
        if not evs:
            continue
        m = medir_noite([s for l in ls for s in sessoes.get(l['location'], [])], evs)
        insts = []
        for l in ls:
            quem = {e['user_id'] if e['user_id'] is not None else e['display_name'] for e in eventos.get(l['location'], []) if e['type'] == 'OnPlayerJoined'}
            insts.append({'mundo': l['mundo'], 'worldId': l['worldId'], 'instancia': l['location'].split('~')[0], 'unicos': len(quem)})
        insts.sort(key=lambda i: -i['unicos'])
        noites.append({'noite': noite, 'mundo': insts[0]['mundo'], 'worldId': insts[0]['worldId'], 'instancias': insts, **m})
    agora = int(datetime.now(timezone.utc).timestamp() * 1000)
    return {'ferramenta': 'many-vrcx', 'versao': 1, 'geradoEm': iso(agora), 'grupo': GRUPO_MANY, 'noites': noites}


def banco_configurado(pasta):
    # o VRCX permite guardar o banco em outro lugar (VRCX_DatabaseLocation no VRCX.json)
    try:
        with open(os.path.join(pasta, 'VRCX.json'), encoding='utf-8-sig') as f:
            config = json.load(f)
    except (OSError, ValueError):
        return None
    return next((v for k, v in config.items() if k.lower() == 'vrcx_databaselocation' and v), None)


def achar_banco():
    pasta = os.path.join(os.environ.get('APPDATA', ''), 'VRCX')
    for p in (os.environ.get('VRCX_DB'), banco_configurado(pasta), os.path.join(pasta, 'VRCX.sqlite3')):
        if p and os.path.exists(p):
            return p
    return None


def diagnostico(caminho):
    # quando não há festas: mostra o que tem no banco aberto, para saber se é o banco certo
    linhas = [f'Banco: {caminho} ({os.path.getsize(caminho) / 1048576:.1f} MB)']
    try:
        db = abrir_banco(caminho)
        n, ini, fim = db.execute('select count(*), min(created_at), max(created_at) from gamelog_location').fetchone()
        grupos = db.execute("select count(distinct location) from gamelog_location where location like '%group(grp_%'").fetchone()[0]
        db.close()
        periodo = f', de {data_br(ini[:10])} a {data_br(fim[:10])}' if n else ''
        linhas.append(f'Mundos visitados gravados: {n}{periodo}. Instâncias de grupos: {grupos}.')
    except (sqlite3.Error, TypeError) as e:
        linhas.append(str(e))
    return '\n'.join(linhas)


def area_de_trabalho():
    # a Área de Trabalho pode estar no OneDrive: pergunta ao Windows em vez de supor a pasta
    try:
        class GUID(ctypes.Structure):
            _fields_ = [('a', ctypes.c_ulong), ('b', ctypes.c_ushort), ('c', ctypes.c_ushort), ('d', ctypes.c_ubyte * 8)]
        u = uuid.UUID('{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}')
        guid = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        saida = ctypes.c_wchar_p()
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(saida))
        if saida.value:
            return Path(saida.value)
    except Exception:
        pass
    return Path.home() / 'Desktop'


def gravar(dados, destino):
    with open(destino, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, separators=(',', ':'))


def data_br(noite):
    return '/'.join(reversed(noite.split('-')))


def versao_num(v):
    return tuple(int(x) for x in v.lstrip('v').split('.'))


def buscar_atualizacao():
    # última versão publicada no GitHub; sem internet ou sem novidade, devolve None e o app segue normal
    pedido = urllib.request.Request(f'https://api.github.com/repos/{REPO}/releases/latest',
                                    headers={'User-Agent': 'ManyVRCX', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(pedido, timeout=8) as r:
        rel = json.load(r)
    asset = next((a for a in rel.get('assets', []) if a['name'] == 'ManyVRCX.exe'), None)
    if asset and versao_num(rel['tag_name']) > versao_num(VERSAO):
        return {'versao': rel['tag_name'].lstrip('v'), 'url': asset['browser_download_url'], 'tamanho': asset['size']}
    return None


def instalar_atualizacao(nova):
    # o Windows não deixa trocar o .exe aberto: baixa ao lado e um .cmd troca depois que este programa fechar
    atual = Path(sys.executable)
    baixado = atual.with_name(atual.stem + '.novo')
    pedido = urllib.request.Request(nova['url'], headers={'User-Agent': 'ManyVRCX'})
    with urllib.request.urlopen(pedido, timeout=60) as r, open(baixado, 'wb') as f:
        shutil.copyfileobj(r, f)
    if baixado.stat().st_size != nova['tamanho']:
        baixado.unlink()
        raise RuntimeError('O download veio incompleto. Tente de novo.')
    bat = Path(tempfile.gettempdir()) / 'many-vrcx-atualizar.cmd'
    bat.write_text('\r\n'.join([
        '@echo off', 'set PYINSTALLER_RESET_ENVIRONMENT=1', 'set n=0', ':tenta', 'timeout /t 1 /nobreak >nul',
        f'move /y "{baixado}" "{atual}" >nul 2>&1', 'if not errorlevel 1 goto pronto', 'set /a n+=1', 'if %n% lss 20 goto tenta',
        ':pronto', f'start "" "{atual}"', 'del "%~f0"']), encoding='mbcs')
    subprocess.Popen(['cmd', '/c', str(bat)], creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)


def caminho_enviadas():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ManyVRCX' / 'exportadas.json'


def ler_exportadas():
    # noites que esta pessoa já exportou: aparecem com o aviso "já exportada"
    try:
        return set(json.loads(caminho_enviadas().read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return set()


def guardar_exportadas(noites):
    try:
        c = caminho_enviadas()
        c.parent.mkdir(parents=True, exist_ok=True)
        c.write_text(json.dumps(sorted(ler_exportadas() | set(noites))), encoding='utf-8')
    except OSError:
        pass


def janela():
    import tkinter as tk
    from tkinter import filedialog

    FUNDO, CARTAO, TEXTO, APAGADO, COR = '#090a13', '#141626', '#f2f4ff', '#9aa0c0', '#17d8c8'
    raiz = tk.Tk()
    raiz.title(f'Many VRCX {VERSAO}')
    raiz.configure(bg=FUNDO, padx=24, pady=20)
    raiz.geometry('560x600')
    raiz.minsize(480, 420)
    try:
        raiz.iconbitmap(os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))), 'many.ico'))
    except tk.TclError:
        pass
    estado = {'banco': achar_banco(), 'dados': None, 'marcas': {}, 'arquivo': None, 'nova': None}

    def botao(pai, txt, acao, cheio=False):
        return tk.Button(pai, text=txt, command=acao, relief='flat', bd=0, padx=14, pady=7, cursor='hand2', font=('Segoe UI Semibold', 10),
                         bg=COR if cheio else CARTAO, fg='#04121a' if cheio else TEXTO, activebackground=COR, activeforeground='#04121a')

    aviso = tk.Frame(raiz, bg=COR)  # só aparece quando há versão nova
    aviso_txt = tk.Label(aviso, bg=COR, fg='#04121a', font=('Segoe UI Semibold', 10), anchor='w', padx=12, pady=8)
    aviso_txt.pack(side='left', fill='x', expand=True)
    tk.Label(raiz, text='Festas do grupo Many', bg=FUNDO, fg=TEXTO, font=('Segoe UI Semibold', 17), anchor='w').pack(fill='x')
    tk.Label(raiz, text='Marque as festas que quer exportar. Sai só quem estava nelas e por quanto tempo; o resto do seu histórico fica no seu PC.',
             bg=FUNDO, fg=APAGADO, font=('Segoe UI', 10), justify='left', wraplength=500, anchor='w').pack(fill='x', pady=(4, 10))

    rodape = tk.Frame(raiz, bg=FUNDO)
    rodape.pack(side='bottom', fill='x', pady=(10, 0))
    status = tk.Label(raiz, bg=FUNDO, fg=TEXTO, font=('Segoe UI', 10), justify='left', wraplength=500, anchor='w')
    status.pack(side='bottom', fill='x', pady=(10, 0))
    atalhos = tk.Frame(raiz, bg=FUNDO)
    atalhos.pack(side='bottom', fill='x', pady=(8, 0))

    moldura = tk.Frame(raiz, bg=CARTAO)
    moldura.pack(fill='both', expand=True)
    tela = tk.Canvas(moldura, bg=CARTAO, highlightthickness=0)
    barra = tk.Scrollbar(moldura, command=tela.yview)
    tela.configure(yscrollcommand=barra.set)
    barra.pack(side='right', fill='y')
    tela.pack(side='left', fill='both', expand=True)
    lista = tk.Frame(tela, bg=CARTAO)
    item = tela.create_window((0, 0), window=lista, anchor='nw')
    lista.bind('<Configure>', lambda e: tela.configure(scrollregion=tela.bbox('all')))
    tela.bind('<Configure>', lambda e: tela.itemconfigure(item, width=e.width))
    tela.bind_all('<MouseWheel>', lambda e: tela.yview_scroll(-e.delta // 120, 'units'))

    def contar(*_):
        n = sum(v.get() for v in estado['marcas'].values())
        gerar.config(state='normal' if n else 'disabled', text=f'Exportar {n} festa(s)' if n else 'Exportar')

    def marcar(valor):
        for v in estado['marcas'].values():
            v.set(valor)

    def mostrar(dados):
        for w in lista.winfo_children():
            w.destroy()
        estado['dados'], estado['marcas'] = dados, {}
        ja = ler_exportadas()
        for n in reversed(dados['noites']):
            v = tk.IntVar(value=0)
            v.trace_add('write', contar)
            estado['marcas'][n['noite']] = v
            texto = f"{data_br(n['noite'])}   {n['mundo']}   ({n['unicos']} pessoas)" + ('   · já exportada' if n['noite'] in ja else '')
            tk.Checkbutton(lista, text=texto, variable=v, anchor='w', bg=CARTAO, fg=TEXTO, selectcolor=FUNDO, activebackground=CARTAO, activeforeground=TEXTO,
                           font=('Segoe UI', 10), padx=10, pady=4).pack(fill='x')
        status.config(text=f"{len(dados['noites'])} festa(s) encontrada(s) neste VRCX." if dados['noites']
                      else 'Não achei festas do grupo Many neste banco. Se o VRCX guarda os dados em outro lugar, clique em "Procurar banco".\n' + diagnostico(estado['banco']))
        contar()

    def carregar():
        status.config(text='Lendo o VRCX, pode levar alguns segundos…')

        def trabalho():
            try:
                dados = extrair(estado['banco'])
                raiz.after(0, lambda: mostrar(dados))
            except Exception as e:
                msg = str(e)
                raiz.after(0, lambda: status.config(text='Não consegui ler o banco do VRCX.\n' + msg))
        threading.Thread(target=trabalho, daemon=True).start()

    def exportar():
        escolhidas = [k for k, v in estado['marcas'].items() if v.get()]
        dados = dict(estado['dados'], noites=[n for n in estado['dados']['noites'] if n['noite'] in escolhidas])
        destino = area_de_trabalho() / f"many-vrcx-{datetime.now().strftime('%Y-%m-%d')}.json"
        try:
            gravar(dados, destino)
        except OSError as e:
            status.config(text='Não consegui salvar o arquivo.\n' + str(e))
            return
        guardar_exportadas(escolhidas)
        estado['arquivo'] = destino
        status.config(text=f'Pronto! {destino.name} está na sua Área de Trabalho.\nAgora abra o Many Planner, vá em Métricas e clique em "Enviar arquivo".')
        pasta.pack(side='left', padx=(8, 0))

    def procurar():
        p = filedialog.askopenfilename(title='Onde está o VRCX.sqlite3?', filetypes=[('Banco do VRCX', '*.sqlite3'), ('Todos', '*.*')])
        if p:
            estado['banco'] = p
            carregar()

    def atualizar():
        aviso_txt.config(text='Baixando a versão nova…')
        botao_nova.config(state='disabled')

        def trabalho():
            try:
                instalar_atualizacao(estado['nova'])
                raiz.after(0, raiz.destroy)
            except Exception as e:
                msg = str(e)
                raiz.after(0, lambda: (aviso_txt.config(text='Não deu para atualizar: ' + msg[:60]), botao_nova.config(state='normal')))
        threading.Thread(target=trabalho, daemon=True).start()

    def olhar_versao():
        try:
            nova = buscar_atualizacao()
        except Exception:
            return
        if nova and getattr(sys, 'frozen', False):
            estado['nova'] = nova
            raiz.after(0, lambda: (aviso_txt.config(text=f"Versão {nova['versao']} disponível"), aviso.pack(fill='x', pady=(0, 12), before=raiz.winfo_children()[1])))

    botao_nova = botao(aviso, 'Atualizar agora', atualizar)
    botao_nova.pack(side='right', padx=6, pady=4)
    botao(atalhos, 'Marcar todas', lambda: marcar(1)).pack(side='left')
    botao(atalhos, 'Desmarcar todas', lambda: marcar(0)).pack(side='left', padx=(8, 0))
    gerar = botao(rodape, 'Exportar', exportar, True)
    gerar.pack(side='left')
    gerar.config(state='disabled')
    pasta = botao(rodape, 'Mostrar arquivo', lambda: subprocess.Popen(['explorer', '/select,', str(estado['arquivo'])]))
    botao(rodape, 'Procurar banco', procurar).pack(side='right')
    if estado['banco']:
        carregar()
    else:
        status.config(text='Não achei o VRCX neste PC. Se ele estiver em outro lugar, clique em "Procurar banco" e escolha o arquivo VRCX.sqlite3.')
    threading.Thread(target=olhar_versao, daemon=True).start()
    raiz.mainloop()


if __name__ == '__main__':
    if '--saida' in sys.argv:
        a = sys.argv
        gravar(extrair(a[a.index('--banco') + 1] if '--banco' in a else achar_banco()), a[a.index('--saida') + 1])
    else:
        janela()
