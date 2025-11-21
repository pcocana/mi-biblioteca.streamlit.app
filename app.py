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
        display: inline-block; padding: 5px 10px; margin: 0 2px;
        border-radius: 4px; text-decoration: none; color: white !important;
        font-size: 11px; font-weight: bold; text-align: center; transition: 0.2s;
    }
    .bf { background-color: #341f97; } 
    .bl { background-color: #fbc531; color: #2f3640 !important; } 
    .gg { background-color: #7f8fa6; } 
    .cot-btn:hover { opacity: 0.8; transform: translateY(-1px); }
</style>
""", unsafe_allow_html=True)

st.title("📚 Gestor Bibliotecario V48 (Deep Match)")
st.markdown("Mejora: **Algoritmo 'Deep Match'**. Detecta títulos dentro de citas académicas largas, ignora comillas y editoriales.")

# --- FUNCIONES DE LIMPIEZA MEJORADAS ---

def limpiar_texto(texto):
    if pd.isna(texto): return ""
    t = str(texto).lower()
    # 1. Quitar URLs
    t = re.sub(r'http\S+|www\.\S+', '', t) 
    # 2. Quitar Años entre parentesis
    t = re.sub(r'\(\d{4}\)', '', t) 
    # 3. Quitar Comillas Tipográficas (El problema de tus archivos nuevos)
    t = t.replace('“', '').replace('”', '').replace('"', '').replace("'", "")
    # 4. Normalizar tildes
    t = t.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    # 5. Dejar solo alfanuméricos y espacios
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    return " ".join(t.split())

def generar_query_busqueda(raw_ref):
    if pd.isna(raw_ref): return ""
    s = str(raw_ref)
    # Limpieza básica para busqueda web
    s_clean = s.replace('“', '').replace('”', '').replace('"', '')
    
    year = ""
    year_match = re.search(r'\b(19|20)\d{2}\b', s_clean)
    if year_match: year = year_match.group(0)
    
    # Intentar sacar lo más relevante (quitar urls, parentesis)
    core = re.sub(r'http\S+', '', s_clean)
    core = re.sub(r'\(\d{4}\)', '', core)
    # Quedarse con las primeras 10 palabras si es muy largo
    words = core.split()
    if len(words) > 10: core = " ".join(words[:10])
    
    core = re.sub(r'[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ ]', ' ', core)
    query = f"{core} {year}".strip()
    return " ".join(query.split())

def tokenize(str_val):
    if not str_val: return []
    return [w for w in limpiar_texto(str(str_val)).split() if len(w) > 2]

def es_articulo_real(texto):
    t = str(texto).lower()
    # Lista estricta para no confundir libros
    palabras_clave = ['revista', 'journal', 'doi.org', 'issn', 'transactions', 'proceedings', 'arxiv']
    return any(p in t for p in palabras_clave)

def cargar_archivo(uploaded_file):
    if uploaded_file is None: return None
    # Prioridad Excel
    if uploaded_file.name.endswith('.xlsx'):
        try:
            uploaded_file.seek(0)
            return pd.read_excel(uploaded_file, engine='openpyxl')
        except: pass
    # Prioridad CSV
    try: uploaded_file.seek(0); return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='utf-8')
    except: pass
    try: uploaded_file.seek(0); return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='latin-1')
    except: pass
    return None

# --- LÓGICA DE VALIDACIÓN V48 (DEEP MATCH) ---
def validar_match_profundo(clean_ref, ref_tokens, book):
    """
    Algoritmo asimétrico: Busca si el libro (pequeño) cabe dentro de la referencia (grande).
    """
    # 1. TÍTULO (Partial Token Set Ratio)
    # Pregunta: ¿El título del libro está contenido en la referencia?
    # Esto permite que "Optical Interconnects" haga match con "Optical Interconnects for Future Data Center..."
    # Usamos 'partial_token_set_ratio' que es muy potente para esto.
    
    score_titulo = fuzz.partial_token_set_ratio(book['cleanTitle'], clean_ref)
    
    # 2. AUTOR (Apellido Check)
    # Si el título coincide muy bien, verificamos si AL MENOS UN APELLIDO del autor está.
    hits_a = 0
    if len(book['aTokens']) > 0:
        hits_a = sum(1 for a in book['aTokens'] if a in ref_tokens)
    else:
        # Si catálogo no tiene autor, somos neutrales
        hits_a = 1 

    # --- REGLAS DE DECISIÓN V48 ---
    
    # CASO A: Título está CASI EXACTO dentro de la referencia
    if score_titulo >= 90:
        if hits_a > 0: return 100 # Título excelente + Autor presente -> MATCH SEGURO
        else: return 45 # Título excelente pero autor no aparece -> DUDOSO (Probablemente edición de otro autor)

    # CASO B: Título muy parecido (>80)
    if score_titulo >= 80:
        if hits_a > 0: return 85 # Muy buen candidato
        return 30

    # CASO C: Título regular
    if score_titulo >= 65:
        if hits_a > 0: return 70 # El autor salva el match
    
    return 0

@st.cache_data
def procesar_datos(file_ref, file_cat):
    df_ref = cargar_archivo(file_ref)
    df_cat = cargar_archivo(file_cat)
    if df_ref is None or df_cat is None: return pd.DataFrame()

    df_cat.columns = df_cat.columns.str.lower().str.strip()
    
    # Detección Referencias (V45)
    col_ref = None
    if len(df_ref.columns) == 1: col_ref = df_ref.columns[0]
    else:
        candidatos = [c for c in df_ref.columns if 'ref' in str(c).lower() or 'bib' in str(c).lower()]
        col_ref = candidatos[0] if candidatos else df_ref.columns[0]

    # Detección Catálogo
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
        
        # Limpieza extra para el catálogo (quitar simbolos)
        clean_t = limpiar_texto(title)
        
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
                'cleanTitle': clean_t # Usamos el titulo limpio completo para fuzz.partial
            }
            stock_map[key] = obj
            catalogo_objs.append(obj)

    catalogo_final = list(stock_map.values())
    # Índice para búsqueda rápida inicial (RapidFuzz)
    titulos_busqueda = [c['cleanTitle'] for c in catalogo_final]
    
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
        
        elif len(clean_ref) > 5:
            # 1. Extracción de Candidatos (Top 20)
            # Usamos partial_token_set_ratio ya desde el principio para atrapar titulos escondidos
            matches = process.extract(clean_ref, titulos_busqueda, scorer=fuzz.partial_token_set_ratio, limit=20)
            
            best_score = 0
            best_match = None

            for match_tuple in matches:
                _, _, match_idx = match_tuple
                book = catalogo_final[match_idx]
                
                # 2. Validación Profunda V48
                score = validar_match_profundo(clean_ref, ref_tokens, book)
                
                if score > best_score:
                    best_score = score
                    best_match = book

            if best_score >= 70: # Umbral V48
                stock = best_match['stock']
                match_nom = best_match['origTitle']
                estado = "EN BIBLIOTECA" if stock > 0 else "FALTANTE (Stock 0)"
                obs = f"Match: {match_nom} (Confianza: {best_score}%)"
            else:
                estado = "FALTANTE"
                obs = "Sin coincidencia suficiente"

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
        st.download_button("📥 Descargar Excel", buf, "Resultado_Final_V48.xlsx")
