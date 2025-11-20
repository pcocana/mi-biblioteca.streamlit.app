import streamlit as st
import pandas as pd
import re
from rapidfuzz import process, fuzz
import io

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Gestor Bibliotecario AI", page_icon="📚", layout="wide")

st.markdown("""
<style>
    .stButton button { width: 100%; }
    .cot-btn {
        display: inline-block; padding: 6px 12px; margin: 0 2px;
        border-radius: 4px; text-decoration: none; color: white !important;
        font-size: 12px; font-weight: bold; text-align: center; transition: 0.2s;
    }
    .bf { background-color: #341f97; } 
    .bl { background-color: #fbc531; color: #2f3640 !important; } 
    .gg { background-color: #7f8fa6; } 
    .cot-btn:hover { opacity: 0.8; transform: translateY(-1px); }
</style>
""", unsafe_allow_html=True)

st.title("📚 Gestor Bibliotecario V45")
st.markdown("Mejora: **Soporte Nativo de Excel**. Sube tu lista directamente en .xlsx sin convertirla a CSV.")

# --- FUNCIONES ---

def limpiar_texto(texto):
    if pd.isna(texto): return ""
    t = str(texto).lower()
    t = re.sub(r'http\S+|www\.\S+', '', t) 
    t = re.sub(r'\(\d{4}\)', '', t) 
    t = t.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    return " ".join(t.split())

def generar_query_busqueda(raw_ref):
    if pd.isna(raw_ref): return ""
    s = str(raw_ref)
    year = ""
    year_match = re.search(r'\b(19|20)\d{2}\b', s)
    if year_match: year = year_match.group(0)
    
    s_clean = re.sub(r'\(\d{4}\)', '', s)
    parts = s_clean.split('.')
    core_text = ""
    count = 0
    for p in parts:
        if len(p.strip()) > 2:
            core_text += p + " "
            count += 1
        if count >= 2: break
    
    if len(core_text) < 5:
        words = s_clean.split()
        core_text = " ".join(words[:8])

    core_text = re.sub(r'[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ ]', ' ', core_text)
    query = f"{core_text} {year}".strip()
    return " ".join(query.split())

def tokenize(str_val):
    if not str_val: return []
    return [w for w in limpiar_texto(str(str_val)).split() if len(w) > 2]

def es_articulo_real(texto):
    t = str(texto).lower()
    palabras_clave = ['revista', 'journal', 'doi.org', 'issn', 'transactions', 'proceedings']
    return any(p in t for p in palabras_clave)

def cargar_archivo(uploaded_file):
    """Carga inteligente: Prioriza Excel si es .xlsx, sino prueba CSVs"""
    if uploaded_file is None: return None
    
    # Detección por extensión para evitar el error de 'zip file'
    if uploaded_file.name.endswith('.xlsx'):
        try:
            uploaded_file.seek(0)
            return pd.read_excel(uploaded_file, engine='openpyxl')
        except Exception as e:
            st.warning(f"Intentando leer Excel como CSV por seguridad... ({e})")
    
    # Fallback o CSV directo
    try: uploaded_file.seek(0); return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='utf-8')
    except: pass
    try: uploaded_file.seek(0); return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='latin-1')
    except: pass
    
    return None

# --- VALIDACIÓN ---
def validar_match(ref_tokens, book):
    t_hits = sum(1 for t in book['tTokens'] if t in ref_tokens)
    t_len = len(book['tTokens'])
    if t_len == 0: return 0
    ratio_titulo = t_hits / t_len
    
    len_diff = abs(len(ref_tokens) - t_len)
    if len_diff > 3 and ratio_titulo < 1.0: ratio_titulo -= 0.2

    a_hits = 0
    if len(book['aTokens']) > 0:
        a_hits = sum(1 for a in book['aTokens'] if a in ref_tokens)
        ratio_autor = a_hits / len(book['aTokens'])
    else:
        ratio_autor = 0.5 

    if ratio_titulo == 1.0 and ratio_autor > 0: return 100
    if ratio_titulo > 0.8:
        if len(book['aTokens']) > 0:
            return 90 if ratio_autor > 0 else 40
        return 75
    if ratio_titulo > 0.6:
        return 80 if ratio_autor > 0.5 else 0

    return 0

@st.cache_data
def procesar_datos(file_ref, file_cat):
    df_ref = cargar_archivo(file_ref)
    df_cat = cargar_archivo(file_cat)
    if df_ref is None or df_cat is None: return pd.DataFrame()

    df_cat.columns = df_cat.columns.str.lower().str.strip()
    
    # --- DETECCIÓN INTELIGENTE DE COLUMNA REFERENCIA V45 ---
    # Si subes un Excel con 1 sola columna, la usa.
    # Si subes el Excel antiguo con muchas columnas, busca la correcta.
    col_ref = None
    
    if len(df_ref.columns) == 1:
        col_ref = df_ref.columns[0]
    else:
        # Buscar 'referencias' o 'bibliografia'
        candidatos = [c for c in df_ref.columns if 'ref' in str(c).lower() or 'bib' in str(c).lower()]
        if candidatos:
            col_ref = candidatos[0]
        else:
            # Si no encuentra nombre, usa la primera columna por defecto
            col_ref = df_ref.columns[0]

    # Catálogo
    try:
        col_tit = [c for c in df_cat.columns if 'tit' in c][0]
        col_aut = [c for c in df_cat.columns if 'aut' in c][0]
    except:
        st.error("Error: El Catálogo debe tener columnas 'Título' y 'Autor'.")
        return pd.DataFrame()
    
    col_stock = next((c for c in df_cat.columns if any(x in c for x in ['ejem', 'copia', 'stock', 'cant'])), None)

    catalogo_objs = []
    stock_map = {} 

    for idx, row in df_cat.iterrows():
        title = str(row[col_tit])
        author = str(row[col_aut]) if pd.notna(row[col_aut]) else ""
        if len(title) < 2: continue

        t_tokens = tokenize(title)
        a_tokens = tokenize(author)
        key = "_".join(t_tokens) + "|" + "_".join(a_tokens)
        
        qty = 1
        if col_stock and pd.notna(row[col_stock]):
            try: qty = int(row[col_stock])
            except: qty = 1
            
        if key in stock_map:
            stock_map[key]['stock'] += qty
        else:
            obj = {'origTitle': title, 'origAuth': author, 'tTokens': t_tokens, 'aTokens': a_tokens, 'stock': qty, 'cleanTitle': " ".join(t_tokens)}
            stock_map[key] = obj
            catalogo_objs.append(obj)

    catalogo_final = list(stock_map.values())
    titulos_busqueda = [c['cleanTitle'] for c in catalogo_final]
    
    resultados = []
    progress_bar = st.progress(0)
    total = len(df_ref)

    for idx, row in df_ref.iterrows():
        if idx % 10 == 0: progress_bar.progress(min(idx / total, 1.0))
        
        raw = str(row[col_ref])
        ref_tokens = tokenize(raw)
        clean_ref = " ".join(ref_tokens)
        
        stock = 0
        estado = "NO ENCONTRADO"
        match_nom = ""
        tipo = "Libro"
        obs = ""
        
        query_optimizada = generar_query_busqueda(raw)
        q_url = query_optimizada.replace(' ', '+')
        
        link_bf = f"https://www.bookfinder.com/search/?keywords={q_url}&mode=basic&st=sr&ac=qr"
        link_bl = f"https://www.buscalibre.cl/libros/search?q={q_url}"
        link_gg = f"https://www.google.com/search?q={q_url}"

        if es_articulo_real(raw):
            tipo = "Artículo"
            estado = "VERIFICAR ONLINE"
            link_bf = f"https://scholar.google.com/scholar?q={q_url}"
            link_gg = f"https://scholar.google.com/scholar?q={q_url}"
        
        elif len(ref_tokens) > 1:
            matches = process.extract(clean_ref, titulos_busqueda, scorer=fuzz.token_set_ratio, limit=15)
            best_score = 0
            best_match = None

            for match_tuple in matches:
                _, _, match_idx = match_tuple
                book = catalogo_final[match_idx]
                score = validar_match(ref_tokens, book)
                if score > best_score:
                    best_score = score
                    best_match = book

            if best_score >= 75:
                stock = best_match['stock']
                match_nom = best_match['origTitle']
                estado = "EN BIBLIOTECA" if stock > 0 else "FALTANTE (Stock 0)"
                obs = f"Match: {match_nom} (Confianza: {best_score}%)"
            else:
                estado = "FALTANTE"
                obs = "No se encontró coincidencia"

        resultados.append({
            "Referencia": raw,
            "Estado": estado,
            "Stock": stock,
            "Match": match_nom,
            "Tipo": tipo,
            "Observaciones": obs,
            "Link_BF": link_bf, "Link_BL": link_bl, "Link_GG": link_gg
        })
    
    progress_bar.progress(100)
    return pd.DataFrame(resultados)

# --- INTERFAZ ---
c1, c2 = st.columns(2)
f1 = c1.file_uploader("1. Referencias (Excel o CSV)", type=['xlsx','csv'])
f2 = c2.file_uploader("2. Catálogo (Excel o CSV)", type=['xlsx','csv'])

if f1 and f2:
    if st.button("🚀 PROCESAR", type="primary"):
        df = procesar_datos(f1, f2)
        
        m1, m2, m3 = st.columns(3)
        m1.metric("Total", len(df))
        m2.metric("En Biblioteca", len(df[df['Stock']>0]))
        faltantes = df[(df['Stock']==0) & (df['Tipo']=='Libro')]
        m3.metric("Faltantes", len(faltantes))
        
        st.divider()
        st.subheader(f"🛒 Cotizador de Faltantes ({len(faltantes)})")
        if not faltantes.empty:
            for i, r in faltantes.iterrows():
                txt = str(r['Referencia'])[:100] + "..."
                c_txt, c_btn = st.columns([3,2])
                c_txt.write(f"**{txt}**")
                c_btn.markdown(f"""
                    <a href="{r['Link_BF']}" target="_blank" class="cot-btn bf">BookFinder</a>
                    <a href="{r['Link_BL']}" target="_blank" class="cot-btn bl">Buscalibre</a>
                    <a href="{r['Link_GG']}" target="_blank" class="cot-btn gg">Google</a>
                """, unsafe_allow_html=True)
                st.divider()
        else: st.info("No hay faltantes.")
        
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Descargar Excel", buf, "Resultado_Final_V45.xlsx")
