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

st.title("📚 Gestor Bibliotecario V41")
st.markdown("Mejora: **Filtro Anti-Genéricos** (Evita matches falsos con títulos cortos como 'Funciones').")

# --- LÓGICA ---

def limpiar_texto(texto):
    if pd.isna(texto): return ""
    t = str(texto).lower()
    t = re.sub(r'http\S+|www\.\S+', '', t)
    t = re.sub(r'\(\d{4}\)', '', t)
    t = t.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    return " ".join(t.split())

def es_articulo_real(texto):
    t = str(texto).lower()
    palabras_clave = ['revista', 'journal', 'doi.org', 'issn', 'transactions', 'proceedings']
    return any(p in t for p in palabras_clave)

def is_similar(a, b):
    """Comparación flexible para palabras (Topologia ~= Topology)"""
    if a == b: return True
    if abs(len(a) - len(b)) > 3: return False
    return fuzz.ratio(a, b) > 85 # Usamos fuzz ratio para velocidad y precisión

def tokenize(str_val):
    if not str_val: return []
    s = limpiar_texto(str(str_val))
    return [w for w in s.split() if len(w) > 2]

def cargar_archivo(uploaded_file):
    if uploaded_file is None: return None
    try:
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='utf-8')
    except: pass
    try:
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='latin-1')
    except: pass
    try:
        uploaded_file.seek(0)
        return pd.read_excel(uploaded_file, engine='openpyxl')
    except Exception as e:
        st.error(f"Error leyendo: {e}")
        return None

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
        st.error("Error en nombres de columnas.")
        return pd.DataFrame()
    
    col_stock = next((c for c in df_cat.columns if any(x in c for x in ['ejem', 'copia', 'stock', 'cant'])), None)

    # Pre-procesar catálogo con tokens
    catalogo_objs = []
    
    # Mapa para agrupar duplicados
    stock_map = {} 

    for idx, row in df_cat.iterrows():
        title = str(row[col_tit])
        author = str(row[col_aut]) if pd.notna(row[col_aut]) else ""
        
        if len(title) < 2: continue

        t_tokens = tokenize(title)
        a_tokens = tokenize(author)
        
        # Clave única
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
                'cleanTitle': " ".join(t_tokens),
                'cleanAuth': " ".join(a_tokens)
            }
            stock_map[key] = obj
            catalogo_objs.append(obj)

    # Fuse Index para búsqueda rápida (solo por título limpio)
    titulos_limpios = [c['cleanTitle'] for c in catalogo_objs]
    
    resultados = []
    progress_bar = st.progress(0)
    total = len(df_ref)

    for idx, row in df_ref.iterrows():
        if idx % 10 == 0: progress_bar.progress(min(idx / total, 1.0))
        
        raw = str(row[col_ref])
        clean_ref = limpiar_texto(raw)
        ref_tokens = tokenize(raw)
        
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
            # 1. Búsqueda rápida de candidatos por título (RapidFuzz)
            # Extraemos el titulo probable de la referencia (antes del primer punto o paréntesis)
            likely_title = clean_ref
            
            matches = process.extract(likely_title, titulos_limpios, scorer=fuzz.token_set_ratio, limit=10)
            
            best_score = 0
            best_match = None

            for match_tuple in matches:
                match_text, score_fuzzy, match_idx = match_tuple
                book = catalogo_objs[match_idx]
                
                # --- LÓGICA DE VALIDACIÓN ESTRICTA V41 ---
                final_score = 0
                
                # A. Coincidencia de Título (Palabra por palabra)
                hits_t = sum(1 for t in book['tTokens'] if t in ref_tokens)
                ratio_t = hits_t / len(book['tTokens']) if book['tTokens'] else 0
                
                # B. Coincidencia de Autor
                hits_a = sum(1 for a in book['aTokens'] if a in ref_tokens)
                has_author_match = hits_a > 0
                
                # C. REGLA ANTI-GENÉRICOS (El parche clave)
                # Si el título del catálogo es corto (ej: "Funciones", "Matematica")
                is_short_title = len(book['cleanTitle']) < 15
                
                if is_short_title:
                    # ¡EXIGIMOS AUTOR! Si título es corto, el autor DEBE coincidir.
                    if has_author_match:
                        if ratio_t == 1.0: final_score = 100
                    else:
                        # Si título corto y NO hay autor match -> PENALIZACIÓN TOTAL
                        final_score = 0 
                else:
                    # Títulos largos (> 15 chars)
                    if ratio_t > 0.8: # Título muy parecido
                        final_score = 80
                        if has_author_match: final_score += 20 # Bonus autor
                    elif ratio_t > 0.5 and has_author_match: # Título regular + Autor
                        final_score = 70
                
                # Anti-Espejo (Si título == autor)
                if book['cleanTitle'] == book['cleanAuth'] and ratio_t < 1:
                    final_score = 0

                if final_score > best_score:
                    best_score = final_score
                    best_match = book

            if best_score >= 70:
                stock = best_match['stock']
                match_nom = best_match['origTitle']
                estado = "EN BIBLIOTECA" if stock > 0 else "FALTANTE (Stock 0)"
                obs = f"Match: {match_nom} (Score: {best_score})"
            else:
                estado = "FALTANTE"
                obs = "Sin coincidencia válida"

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
        st.subheader("🛒 Cotizador")
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
        st.download_button("📥 Descargar Excel", buf, "Resultado_Final_V41.xlsx")
