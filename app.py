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

st.title("📚 Gestor Bibliotecario V47 (Tanque)")
st.markdown("Mejora: **Resistencia a Fallos**. Si una fila da error, se salta y continúa con el resto.")

# --- FUNCIONES ---

def limpiar_texto(texto):
    if pd.isna(texto): return ""
    t = str(texto).lower()
    t = re.sub(r'http\S+|www\.\S+', '', t) 
    t = re.sub(r'\(\d{4}\)', '', t) 
    t = t.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    return " ".join(t.split())

def extraer_anio(texto):
    if pd.isna(texto): return 0
    s = str(texto)
    match = re.search(r'\b(19|20)\d{2}\b', s)
    if match:
        return int(match.group(0))
    return 0

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

    # --- BLINDAJE DE COLUMNAS V47 ---
    # Convertimos headers a string para evitar error si hay numeros (ej: año 2022 como titulo)
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
        st.error("Error: El Catálogo debe tener columnas 'Título' y 'Autor'.")
        return pd.DataFrame()
    
    col_stock = next((c for c in df_cat.columns if any(x in c for x in ['ejem', 'copia', 'stock', 'cant'])), None)
    col_anio = next((c for c in df_cat.columns if any(x in c for x in ['fecha', 'año', 'ano', 'year', 'publi'])), None)

    catalogo_objs = []
    stock_map = {} 

    # INDEXAR CATÁLOGO
    for idx, row in df_cat.iterrows():
        try: # Try/Except por fila
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
                
            year_book = 0
            if col_anio and pd.notna(row[col_anio]):
                year_book = extraer_anio(row[col_anio])

            if key not in stock_map:
                stock_map[key] = {
                    'origTitle': title,
                    'origAuth': author,
                    'tTokens': t_tokens,
                    'aTokens': a_tokens,
                    'cleanTitle': " ".join(t_tokens),
                    'years_data': {}
                }
            
            if year_book not in stock_map[key]['years_data']:
                stock_map[key]['years_data'][year_book] = 0
            stock_map[key]['years_data'][year_book] += qty
        except:
            continue # Si falla una fila del catálogo, la saltamos

    catalogo_final = list(stock_map.values())
    titulos_busqueda = [c['cleanTitle'] for c in catalogo_final]
    
    resultados = []
    progress_bar = st.progress(0)
    total = len(df_ref)
    errores_count = 0

    for idx, row in df_ref.iterrows():
        if idx % 10 == 0: progress_bar.progress(min(idx / total, 1.0))
        
        try: # --- TRY/EXCEPT MAESTRO PARA REFERENCIAS ---
            raw = str(row[col_ref])
            ref_tokens = tokenize(raw)
            clean_ref = " ".join(ref_tokens)
            
            ref_year = extraer_anio(raw)
            
            stock_valid = 0
            stock_old = 0
            estado = "NO ENCONTRADO"
            match_nom = ""
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
                    match_nom = best_match['origTitle']
                    years_found = []
                    years_discarded = []
                    strict_filter = (ref_year > 0)
                    
                    for y, q in best_match['years_data'].items():
                        if not strict_filter or y == 0 or y >= ref_year:
                            stock_valid += q
                            years_found.append(f"{y} ({q})")
                        else:
                            stock_old += q
                            years_discarded.append(f"{y} ({q})")
                    
                    if stock_valid > 0:
                        estado = "EN BIBLIOTECA"
                        obs = f"✅: {', '.join(years_found)}"
                        if stock_old > 0: obs += f" | ❌ Antiguos: {', '.join(years_discarded)}"
                    elif stock_old > 0:
                        estado = "FALTANTE (Edición Vieja)"
                        obs = f"❌ Solo antiguos: {', '.join(years_discarded)}"
                    else:
                        estado = "FALTANTE (Stock 0)"
                        obs = f"Match: {match_nom} (Sin ejemplares)"
                else:
                    estado = "FALTANTE"
                    obs = "No se encontró coincidencia"

            resultados.append({
                "Referencia": raw,
                "Estado": estado,
                "Stock Vigente": stock_valid,
                "Stock Descartado": stock_old,
                "Match": match_nom,
                "Ref Año": ref_year if ref_year > 0 else "N/D",
                "Tipo": tipo,
                "Observaciones": obs,
                "Link_BF": link_bf, "Link_BL": link_bl, "Link_GG": link_gg
            })
        except:
            errores_count += 1
            continue # Saltamos la línea corrupta
    
    progress_bar.progress(100)
    if errores_count > 0:
        st.warning(f"⚠️ Se saltaron {errores_count} líneas por errores de formato en el archivo.")
        
    return pd.DataFrame(resultados)

# --- INTERFAZ ---
c1, c2 = st.columns(2)
f1 = c1.file_uploader("1. Referencias", type=['csv','xlsx'])
f2 = c2.file_uploader("2. Catálogo", type=['csv','xlsx'])

if f1 and f2:
    if st.button("🚀 PROCESAR", type="primary"):
        df = procesar_datos(f1, f2)
        
        if not df.empty:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total", len(df))
            m2.metric("En Biblioteca (Vigentes)", len(df[df['Stock Vigente']>0]))
            
            faltantes_reales = df[((df['Stock Vigente']==0)) & (df['Tipo']=='Libro')]
            m3.metric("Faltantes Totales", len(faltantes_reales))
            
            desactualizados = df[(df['Stock Vigente']==0) & (df['Stock Descartado']>0)]
            m4.metric("Solo Ed. Antiguas", len(desactualizados))
            
            st.divider()
            st.subheader(f"🛒 Faltantes o Desactualizados ({len(faltantes_reales)})")
            
            if not faltantes_reales.empty:
                for i, r in faltantes_reales.iterrows():
                    txt = str(r['Referencia'])[:100] + "..."
                    obs_alert = ""
                    if r['Stock Descartado'] > 0:
                        obs_alert = f"⚠️ **OJO:** Tienes {r['Stock Descartado']} copias pero son antiguas ({r['Observaciones']})"
                    
                    c_txt, c_btn = st.columns([3,2])
                    with c_txt:
                        st.write(f"**{txt}**")
                        if obs_alert: st.caption(obs_alert)
                    with c_btn:
                        st.markdown(f"""
                            <a href="{r['Link_BF']}" target="_blank" class="cot-btn bf">BookFinder</a>
                            <a href="{r['Link_BL']}" target="_blank" class="cot-btn bl">Buscalibre</a>
                            <a href="{r['Link_GG']}" target="_blank" class="cot-btn gg">Google</a>
                        """, unsafe_allow_html=True)
                    st.divider()
            else: st.info("¡Biblioteca al día! Todo el material está disponible.")
            
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False)
            st.download_button("📥 Descargar Excel", buf, "Resultado_Final_V47.xlsx")
        else:
            st.error("El archivo resultante está vacío. Revisa que tus Excels tengan datos.")
