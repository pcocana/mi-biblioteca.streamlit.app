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

st.title("📚 Gestor Bibliotecario V42 (Modo Estricto)")
st.markdown("Mejora: **Validación Cruzada de Autor**. Si el autor no coincide, se marca como Faltante.")

# --- LÓGICA DE LIMPIEZA ---

def limpiar_texto(texto):
    if pd.isna(texto): return ""
    t = str(texto).lower()
    t = re.sub(r'http\S+|www\.\S+', '', t) # Quitar URLs
    t = re.sub(r'\(\d{4}\)', '', t) # Quitar años
    t = t.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    t = re.sub(r'[^a-z0-9\s]', ' ', t) # Solo letras y numeros
    return " ".join(t.split())

def tokenize(str_val):
    if not str_val: return []
    return [w for w in limpiar_texto(str(str_val)).split() if len(w) > 2]

def es_articulo_real(texto):
    t = str(texto).lower()
    palabras_clave = ['revista', 'journal', 'doi.org', 'issn', 'transactions', 'proceedings']
    return any(p in t for p in palabras_clave)

def cargar_archivo(uploaded_file):
    if uploaded_file is None: return None
    try: uploaded_file.seek(0); return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='utf-8')
    except: pass
    try: uploaded_file.seek(0); return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='latin-1')
    except: pass
    try: uploaded_file.seek(0); return pd.read_excel(uploaded_file, engine='openpyxl')
    except Exception as e: st.error(f"Error: {e}"); return None

# --- LÓGICA DE VALIDACIÓN ESTRICTA (EL CEREBRO V42) ---
def validar_match(ref_tokens, book):
    """
    Retorna un puntaje de confianza (0-100) basado en Título Y Autor.
    """
    # 1. COINCIDENCIA DE TÍTULO (Set Ratio: ignora orden)
    # ¿Cuántas palabras del título del catálogo están en la referencia?
    t_hits = sum(1 for t in book['tTokens'] if t in ref_tokens)
    t_len = len(book['tTokens'])
    if t_len == 0: return 0
    
    ratio_titulo = t_hits / t_len
    
    # Penalización por longitud: Si la referencia es mucho más larga que el título del catálogo
    # Ej: Ref="Cálculo de Variaciones Gelfand" vs Cat="Cálculo" -> Peligroso
    len_diff = abs(len(ref_tokens) - t_len)
    if len_diff > 3 and ratio_titulo < 1.0: 
        ratio_titulo -= 0.2 # Bajamos puntaje si los tamaños son muy distintos

    # 2. COINCIDENCIA DE AUTOR (CRÍTICO)
    # Si el catálogo tiene autor, DEBE aparecer en la referencia
    a_hits = 0
    if len(book['aTokens']) > 0:
        a_hits = sum(1 for a in book['aTokens'] if a in ref_tokens)
        ratio_autor = a_hits / len(book['aTokens'])
    else:
        ratio_autor = 0.5 # Neutro si no hay autor en catálogo (confiamos en título)

    # --- REGLAS DE DECISIÓN ---
    
    # CASO A: Título Perfecto + Autor Existe en Ref
    if ratio_titulo == 1.0 and ratio_autor > 0:
        return 100
    
    # CASO B: Título Muy Bueno (>80%)
    if ratio_titulo > 0.8:
        if len(book['aTokens']) > 0:
            if ratio_autor > 0: return 90 # Título bueno, Autor coincide algo -> APROBADO
            else: return 40 # Título bueno, pero AUTOR NO COINCIDE -> RECHAZADO
        else:
            return 75 # Título bueno, sin autor para validar -> DUDA (Aceptable)

    # CASO C: Título Medio (60-80%)
    if ratio_titulo > 0.6:
        if ratio_autor > 0.5: return 80 # Autor salva el match
        else: return 0 # Si el título no es perfecto y el autor no está -> BASURA

    return 0

@st.cache_data
def procesar_datos(file_ref, file_cat):
    df_ref = cargar_archivo(file_ref)
    df_cat = cargar_archivo(file_cat)
    if df_ref is None or df_cat is None: return pd.DataFrame()

    df_cat.columns = df_cat.columns.str.lower().str.strip()
    df_ref.columns = df_ref.columns.str.lower().str.strip()

    try:
        col_ref = [c for c in df_ref.columns if 'ref' in c or 'bib' in c][0]
        col_tit = [c for c in df_cat.columns if 'tit' in c][0]
        col_aut = [c for c in df_cat.columns if 'aut' in c][0]
    except:
        st.error("Error en columnas.")
        return pd.DataFrame()
    
    col_stock = next((c for c in df_cat.columns if any(x in c for x in ['ejem', 'copia', 'stock', 'cant'])), None)

    # Pre-procesar catálogo (Lista de Objetos para iterar rápido)
    catalogo_objs = []
    
    # Mapa para sumar stock de duplicados antes de procesar
    stock_map = {} 

    for idx, row in df_cat.iterrows():
        title = str(row[col_tit])
        author = str(row[col_aut]) if pd.notna(row[col_aut]) else ""
        
        if len(title) < 2: continue # Ignorar basura

        t_tokens = tokenize(title)
        a_tokens = tokenize(author)
        
        # Clave única (Título+Autor) para agrupar
        key = "_".join(t_tokens) + "|" + "_".join(a_tokens)
        
        qty = 1
        if col_stock and pd.notna(row[col_stock]):
            try: qty = int(row[col_stock])
            except: qty = 1
            
        if key in stock_map:
            stock_map[key]['stock'] += qty
        else:
            obj = {
                'origTitle': title,
                'origAuth': author,
                'tTokens': t_tokens,
                'aTokens': a_tokens,
                'stock': qty,
                'cleanTitle': " ".join(t_tokens) # Para búsqueda rápida
            }
            stock_map[key] = obj
            catalogo_objs.append(obj)

    # Lista final optimizada
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
        
        q_cotiz = re.sub(r'[^a-zA-Z0-9 ]', '', raw).replace(' ', '+')
        link_bf = f"https://www.bookfinder.com/search/?keywords={q_cotiz}&mode=basic&st=sr&ac=qr"
        link_bl = f"https://www.buscalibre.cl/libros/search?q={q_cotiz}"
        link_gg = f"https://www.google.com/search?tbm=bks&q={q_cotiz}"

        if es_articulo_real(raw):
            tipo = "Artículo"
            estado = "VERIFICAR ONLINE"
            link_bf = f"https://scholar.google.com/scholar?q={q_cotiz}"
        
        elif len(ref_tokens) > 1:
            # 1. Búsqueda Rápida: Traer los 10 candidatos más parecidos por título
            matches = process.extract(clean_ref, titulos_busqueda, scorer=fuzz.token_set_ratio, limit=15)
            
            best_score = 0
            best_match = None

            # 2. Validación Fina (El Candado de Autor)
            for match_tuple in matches:
                _, _, match_idx = match_tuple
                book = catalogo_final[match_idx]
                
                # Función de validación estricta V42
                score = validar_match(ref_tokens, book)
                
                if score > best_score:
                    best_score = score
                    best_match = book

            # 3. Decisión Final
            if best_score >= 75: # Umbral alto para seguridad
                stock = best_match['stock']
                match_nom = best_match['origTitle']
                estado = "EN BIBLIOTECA" if stock > 0 else "FALTANTE (Stock 0)"
                obs = f"Match: {match_nom} (Confianza: {best_score}%)"
            else:
                estado = "FALTANTE"
                obs = "No se encontró coincidencia de Autor+Título suficiente"

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
f1 = c1.file_uploader("1. Referencias", type=['csv','xlsx'])
f2 = c2.file_uploader("2. Catálogo", type=['csv','xlsx'])

if f1 and f2:
    if st.button("🚀 PROCESAR", type="primary"):
        df = procesar_datos(f1, f2)
        
        m1, m2, m3 = st.columns(3)
        m1.metric("Total", len(df))
        m2.metric("En Biblioteca", len(df[df['Stock']>0]))
        faltantes = df[(df['Stock']==0) & (df['Tipo']=='Libro')]
        m3.metric("Faltantes", len(faltantes))
        
        st.divider()
        st.subheader(f"🛒 Lista de Faltantes ({len(faltantes)})")
        if not faltantes.empty:
            for i, r in faltantes.iterrows():
                txt = r['Referencia'][:100] + "..."
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
        st.download_button("📥 Descargar Excel", buf, "Resultado_Final_V42.xlsx")
