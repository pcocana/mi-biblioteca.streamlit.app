import streamlit as st
import pandas as pd
import re
from rapidfuzz import process, fuzz
import io

# --- CONFIGURACIÓN VISUAL ---
st.set_page_config(page_title="Gestor Bibliotecario V56", page_icon="🏛️", layout="wide")

# CSS PARA LA TABLA BONITA
st.markdown("""
<style>
    .stButton button { width: 100%; background-color: #2e86de; color: white; font-weight: bold; }
    
    /* Estilos de Tabla Personalizada */
    .styled-table {
        border-collapse: collapse;
        margin: 25px 0;
        font-size: 0.9em;
        font-family: sans-serif;
        min-width: 100%;
        box-shadow: 0 0 20px rgba(0, 0, 0, 0.15);
    }
    .styled-table thead tr {
        background-color: #2e86de;
        color: #ffffff;
        text-align: left;
    }
    .styled-table th, .styled-table td {
        padding: 12px 15px;
        border-bottom: 1px solid #dddddd;
    }
    .styled-table tbody tr:nth-of-type(even) {
        background-color: #f3f3f3;
    }
    .styled-table tbody tr:last-of-type {
        border-bottom: 2px solid #2e86de;
    }
    
    /* Estilos de Botones */
    .cot-btn {
        display: inline-block; padding: 5px 10px; margin: 0 2px;
        border-radius: 4px; text-decoration: none; color: white !important;
        font-size: 11px; font-weight: bold; text-align: center; transition: 0.2s;
        white-space: nowrap;
    }
    .bf { background-color: #341f97; } 
    .bl { background-color: #fbc531; color: #2f3640 !important; } 
    .gg { background-color: #7f8fa6; } 
    .gs { background-color: #4285F4; } /* Google Scholar Blue */
    .cot-btn:hover { opacity: 0.8; transform: translateY(-1px); }
</style>
""", unsafe_allow_html=True)

st.title("🏛️ Gestor Bibliotecario V56")
st.markdown("Diseño Final: **Tabla Interactiva**. Combina el orden de Excel con la potencia de la Web.")

# --- 1. FUNCIONES DE LIMPIEZA Y LÓGICA ---

def limpiar_texto(texto):
    if pd.isna(texto): return ""
    t = str(texto).lower()
    t = t.replace('“', '').replace('”', '').replace('"', '').replace("'", "")
    t = t.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    t = re.sub(r'[^a-z\s]', ' ', t)
    return " ".join(t.split())

def generar_query_busqueda(raw_ref):
    if pd.isna(raw_ref): return ""
    s = str(raw_ref).replace('“', '').replace('”', '')
    year = ""
    match = re.search(r'\b(19|20)\d{2}\b', s)
    if match: year = match.group(0)
    s_clean = re.sub(r'\(\d{4}\)', '', s)
    core = re.sub(r'[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ ]', ' ', s_clean)
    words = core.split()
    short_core = " ".join(words[:12])
    return f"{short_core} {year}".strip()

def es_articulo_real(texto):
    t = str(texto).lower()
    palabras_clave = [' doi.org', 'issn', 'transactions', 'proceedings']
    if 'journal' in t and 'journal of' in t: return True
    return any(p in t for p in palabras_clave)

def extraer_anio(texto):
    if pd.isna(texto): return 0
    match = re.search(r'\b(19|20)\d{2}\b', str(texto))
    return int(match.group(0)) if match else 0

def generar_tokens(texto):
    return set(limpiar_texto(texto).split())

# --- 2. LECTURA MANUAL ROBUSTA ---

def leer_referencias_raw(uploaded_file):
    try:
        content = uploaded_file.getvalue().decode("latin-1")
        lines = content.splitlines()
        data = []
        buffer = ""
        for line in lines:
            line = line.strip()
            if not line: continue
            if "Referencia" in line and "Unidad" in line: continue
            if len(line) < 10 or line.count(';') == 0:
                buffer += " " + line
            else:
                if buffer: data.append(buffer)
                buffer = line
        if buffer: data.append(buffer)
        clean_data = [row.split(';')[0] for row in data if len(row) > 5]
        return pd.DataFrame(clean_data, columns=["Referencias"])
    except Exception as e:
        st.error(f"Error lectura: {e}")
        return pd.DataFrame()

def leer_catalogo_pandas(uploaded_file):
    try:
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, sep=';', encoding='latin-1', on_bad_lines='skip')
    except:
        try:
            uploaded_file.seek(0)
            return pd.read_excel(uploaded_file)
        except: return None

# --- 3. PROCESAMIENTO ---

@st.cache_data
def procesar_bibliografia(file_ref, file_cat):
    df_ref = leer_referencias_raw(file_ref)
    df_cat = leer_catalogo_pandas(file_cat)
    
    if df_ref.empty or df_cat is None: return None

    df_cat.columns = df_cat.columns.astype(str).str.lower().str.strip()
    
    try:
        col_tit = [c for c in df_cat.columns if 'tit' in c][0]
        col_aut = [c for c in df_cat.columns if 'aut' in c][0]
        col_stock = [c for c in df_cat.columns if 'ejem' in c or 'copia' in c][0]
        col_anio = [c for c in df_cat.columns if 'fecha' in c or 'año' in c or 'year' in c][0]
    except:
        st.error("Error columnas catálogo")
        return None

    catalogo_index = {}
    
    for idx, row in df_cat.iterrows():
        titulo = str(row[col_tit])
        autor = str(row[col_aut]) if pd.notna(row[col_aut]) else ""
        try: stock = int(row[col_stock])
        except: stock = 1
        try: anio = int(row[col_anio])
        except: anio = extraer_anio(str(row[col_anio]))

        t_clean = limpiar_texto(titulo)
        if len(t_clean) < 3: continue
        
        clave = t_clean 
        
        if clave not in catalogo_index:
            catalogo_index[clave] = {
                'titulo_oficial': titulo, 'autor_oficial': autor, 'stock_total': 0,
                'detalles_anios': [], 'tokens_titulo': generar_tokens(titulo),
                'tokens_autor': generar_tokens(autor)
            }
        
        catalogo_index[clave]['stock_total'] += stock
        catalogo_index[clave]['detalles_anios'].append(f"{anio} ({stock})")

    claves_catalogo = list(catalogo_index.keys())
    
    resultados = []
    progreso = st.progress(0)
    total_refs = len(df_ref)
    
    for i, row in df_ref.iterrows():
        progreso.progress(min((i+1)/total_refs, 1.0))
        
        raw_ref = str(row["Referencias"])
        if len(raw_ref) < 5: continue
        
        clean_ref = limpiar_texto(raw_ref)
        ref_tokens = generar_tokens(raw_ref)
        ref_anio = extraer_anio(raw_ref)
        
        # Links
        query_url = generar_query_busqueda(raw_ref).replace(" ", "+")
        link_bf = f"https://www.bookfinder.com/search/?keywords={query_url}&mode=basic&st=sr&ac=qr"
        link_bl = f"https://www.buscalibre.cl/libros/search?q={query_url}"
        link_gg = f"https://www.google.com/search?q={query_url}"
        
        tipo = "Libro"
        if es_articulo_real(raw_ref):
            tipo = "Artículo"
            link_bf = f"https://scholar.google.com/scholar?q={query_url}"

        # Match
        match = process.extractOne(clean_ref, claves_catalogo, scorer=fuzz.token_set_ratio)
        estado = "NO ENCONTRADO"
        stock_encontrado = 0
        detalle_match = ""
        info_extra = ""
        
        if match:
            clave_encontrada, puntaje, _ = match
            libro_cat = catalogo_index[clave_encontrada]
            
            autor_coincide = False
            if not libro_cat['tokens_autor']: autor_coincide = True 
            else:
                if len(libro_cat['tokens_autor'].intersection(ref_tokens)) > 0: autor_coincide = True
            
            if puntaje >= 80 and autor_coincide:
                stock_encontrado = libro_cat['stock_total']
                anios_str = ", ".join(libro_cat['detalles_anios'])
                
                if stock_encontrado > 0:
                    estado = "EN BIBLIOTECA"
                    detalle_match = libro_cat['titulo_oficial']
                    info_extra = f"Copias: {anios_str}"
                    if ref_anio > 0:
                        anios_nums = [int(re.search(r'\d+', x).group()) for x in libro_cat['detalles_anios'] if re.search(r'\d+', x)]
                        if anios_nums and max(anios_nums) < ref_anio:
                            estado = "EN BIBLIOTECA (Antiguo)"
                            info_extra += " ⚠️"
                else:
                    estado = "FALTANTE (Stock 0)"
            else:
                estado = "FALTANTE"

        resultados.append({
            "Referencia": raw_ref, "Estado": estado, "Stock": stock_encontrado,
            "Info": info_extra, "Tipo": tipo,
            "Link_BF": link_bf, "Link_BL": link_bl, "Link_GG": link_gg
        })
        
    return pd.DataFrame(resultados)

# --- INTERFAZ ---

c1, c2 = st.columns(2)
f1 = c1.file_uploader("1. Referencias", type=['csv','xlsx'])
f2 = c2.file_uploader("2. Catálogo", type=['csv','xlsx'])

if f1 and f2:
    if st.button("🔍 PROCESAR", type="primary"):
        df = procesar_bibliografia(f1, f2)
        
        if df is not None:
            tot = len(df)
            enc = len(df[df['Stock'] > 0])
            fal = len(df[df['Stock'] == 0])
            
            c_a, c_b, c_c = st.columns(3)
            c_a.metric("Referencias", tot)
            c_b.metric("En Biblioteca", enc)
            c_c.metric("Faltantes", fal)
            
            st.divider()
            st.subheader("🛒 Cotizador de Faltantes")
            
            # --- TABLA HTML PERSONALIZADA (LA MAGIA DE V56) ---
            faltantes_df = df[(df['Stock'] == 0) & (df['Tipo'] == 'Libro')]
            
            if not faltantes_df.empty:
                # Construcción de la tabla HTML
                html_table = """<table class="styled-table"><thead><tr>
                    <th>Referencia Bibliográfica</th>
                    <th style="width:100px">Tipo</th>
                    <th style="width:250px">Acciones de Cotización</th>
                </tr></thead><tbody>"""
                
                for _, row in faltantes_df.iterrows():
                    ref_txt = row['Referencia'][:150] + "..."
                    
                    # Botones
                    if row['Tipo'] == 'Artículo':
                        btns = f"""<a href="{row['Link_BF']}" target="_blank" class="cot-btn gs">Google Scholar</a>"""
                    else:
                        btns = f"""
                        <a href="{row['Link_BF']}" target="_blank" class="cot-btn bf">BookFinder</a>
                        <a href="{row['Link_BL']}" target="_blank" class="cot-btn bl">Buscalibre</a>
                        <a href="{row['Link_GG']}" target="_blank" class="cot-btn gg">Google</a>
                        """
                    
                    html_table += f"""<tr>
                        <td>{ref_txt}</td>
                        <td>{row['Tipo']}</td>
                        <td>{btns}</td>
                    </tr>"""
                
                html_table += "</tbody></table>"
                st.markdown(html_table, unsafe_allow_html=True)
            else:
                st.info("¡Todo encontrado!")

            # Descarga
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False)
            st.download_button("📥 Descargar Excel Completo", buf, "Resultado_V56.xlsx")
