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

st.title("📚 Gestor Bibliotecario V49 (Cascada)")
st.markdown("Mejora: **Lógica en Cascada**. Recupera libros sin autor (Códigos, Manuales) y autores abreviados.")

# --- FUNCIONES ---

def limpiar_texto(texto):
    if pd.isna(texto): return ""
    t = str(texto).lower()
    t = re.sub(r'http\S+|www\.\S+', '', t)
    t = re.sub(r'\(\d{4}\)', '', t) # Quitar años parentesis
    # Mantenemos numeros porque son importantes en química/física (ej: 7ma Edicion)
    t = t.replace('“', '').replace('”', '').replace('"', '').replace("'", "")
    t = t.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    return " ".join(t.split())

def generar_query_busqueda(raw_ref):
    if pd.isna(raw_ref): return ""
    s = str(raw_ref).replace('“', '').replace('”', '')
    year = ""
    year_match = re.search(r'\b(19|20)\d{2}\b', s)
    if year_match: year = year_match.group(0)
    
    s_clean = re.sub(r'\(\d{4}\)', '', s)
    # Tomar las primeras palabras clave
    core = " ".join(re.sub(r'[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ ]', ' ', s_clean).split()[:12])
    return f"{core} {year}".strip()

def tokenize(str_val):
    if not str_val: return []
    return [w for w in limpiar_texto(str(str_val)).split() if len(w) > 2]

def es_articulo_real(texto):
    t = str(texto).lower()
    # Lista reducida para no confundir libros técnicos
    palabras_clave = [' doi.org', 'issn', 'transactions', 'proceedings']
    # Si dice "Journal" pero no parece editorial
    if 'journal' in t and 'journal of' in t: return True
    return any(p in t for p in palabras_clave)

def cargar_archivo(uploaded_file):
    if uploaded_file is None: return None
    if uploaded_file.name.endswith('.xlsx'):
        try:
            uploaded_file.seek(0)
            return pd.read_excel(uploaded_file, engine='openpyxl')
        except: pass
    try: uploaded_file.seek(0); return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='utf-8')
    except: pass
    try: uploaded_file.seek(0); return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='latin-1')
    except: pass
    return None

# --- LÓGICA V49 (CASCADA) ---
def validar_match_cascada(clean_ref, ref_tokens, book):
    """
    Retorna (Score, Metodo_Match)
    """
    # 1. Título Parcial (¿El libro está en la referencia?)
    score_titulo = fuzz.partial_token_set_ratio(book['cleanTitle'], clean_ref)
    
    # 2. Análisis de Autor
    hits_a = 0
    if len(book['aTokens']) > 0:
        # Buscamos si CUALQUIER token del autor del libro está en la referencia
        # Esto arregla "F.S. Hillier" vs "Hillier"
        for a_tok in book['aTokens']:
            if a_tok in ref_tokens:
                hits_a += 1
    
    has_author = (hits_a > 0)
    
    # --- CASCADA DE DECISIÓN ---
    
    # NIVEL 1: Match Perfecto (Título Alto + Autor Presente)
    if score_titulo >= 90 and has_author:
        return 100, "Título y Autor Exactos"

    # NIVEL 2: Título Único / Específico (Para libros sin autor claro o Manuales)
    # Si el título es largo (> 15 letras) y coincide MUY bien, ignoramos al autor.
    # Ej: "Código del Trabajo", "Perry's Chemical Engineers Handbook"
    if score_titulo >= 95 and len(book['cleanTitle']) > 15:
        return 95, "Título Único (Autor Ignorado)"

    # NIVEL 3: Match Flexible (Título Bueno + Autor Presente)
    if score_titulo >= 80 and has_author:
        return 85, "Título Flexible + Autor"

    # NIVEL 4: Título Muy Largo pero coincidencia media
    # Ej: Referencias muy sucias pero el titulo largo se detecta
    if score_titulo >= 85 and len(book['cleanTitle']) > 25:
        return 80, "Título Largo Coincidente"

    # Penalización para títulos cortos genéricos sin autor ("Física", "Química")
    if len(book['cleanTitle']) < 12 and not has_author:
        return 0, "Descarte por Genérico"

    return 0, ""

@st.cache_data
def procesar_datos(file_ref, file_cat):
    df_ref = cargar_archivo(file_ref)
    df_cat = cargar_archivo(file_cat)
    if df_ref is None or df_cat is None: return pd.DataFrame()

    df_cat.columns = df_cat.columns.astype(str).str.lower().str.strip()
    
    # Detección Referencias
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
        st.error("Error: Faltan columnas Título/Autor en catálogo.")
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
                'cleanTitle': clean_t
            }
            stock_map[key] = obj
            catalogo_objs.append(obj)

    catalogo_final = list(stock_map.values())
    # Usamos el título limpio para la búsqueda inicial
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
        match_metodo = ""
        tipo = "Libro"
        obs = ""
        
        q_cotiz = re.sub(r'[^a-zA-Z0-9 ]', '', raw).replace(' ', '+')
        link_bf = f"https://www.bookfinder.com/search/?keywords={q_cotiz}&mode=basic&st=sr&ac=qr"
        link_bl = f"https://www.buscalibre.cl/libros/search?q={q_cotiz}"
        link_gg = f"https://www.google.com/search?q={q_cotiz}"

        if es_articulo_real(raw):
            tipo = "Artículo"
            estado = "VERIFICAR ONLINE"
            link_bf = f"https://scholar.google.com/scholar?q={q_cotiz}"
            link_gg = f"https://scholar.google.com/scholar?q={q_cotiz}"
        
        elif len(clean_ref) > 5:
            # 1. Candidatos (Top 30 para buscar profundo)
            matches = process.extract(clean_ref, titulos_busqueda, scorer=fuzz.partial_token_set_ratio, limit=30)
            
            best_score = 0
            best_match = None
            best_method = ""

            for match_tuple in matches:
                _, _, match_idx = match_tuple
                book = catalogo_final[match_idx]
                
                # 2. Validación V49
                score, metodo = validar_match_cascada(clean_ref, ref_tokens, book)
                
                if score > best_score:
                    best_score = score
                    best_match = book
                    best_method = metodo

            if best_score >= 80: # Umbral de aceptación
                stock = best_match['stock']
                match_nom = best_match['origTitle']
                estado = "EN BIBLIOTECA" if stock > 0 else "FALTANTE (Stock 0)"
                obs = f"Match: {match_nom} | Método: {best_method} ({best_score}%)"
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
        st.download_button("📥 Descargar Excel", buf, "Resultado_Final_V49.xlsx")
