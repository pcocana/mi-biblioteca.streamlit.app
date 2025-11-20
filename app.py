import streamlit as st
import pandas as pd
import re
from rapidfuzz import process, fuzz
import io

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor Bibliotecario AI", page_icon="📚", layout="wide")

# Estilos CSS para botones
st.markdown("""
<style>
    .stButton button { width: 100%; }
    .cot-btn {
        display: inline-block;
        padding: 6px 12px;
        margin: 0 2px;
        border-radius: 4px;
        text-decoration: none;
        color: white !important;
        font-size: 12px;
        font-weight: bold;
        text-align: center;
        transition: 0.2s;
    }
    .bf { background-color: #341f97; } 
    .bl { background-color: #fbc531; color: #2f3640 !important; } 
    .gg { background-color: #7f8fa6; } 
    .cot-btn:hover { opacity: 0.8; transform: translateY(-1px); }
</style>
""", unsafe_allow_html=True)

st.title("📚 Gestor Bibliotecario V40")
st.markdown("Corrección: Los libros con 'Vol.' ya no se confunden con artículos.")

# --- FUNCIONES DE LÓGICA ---

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
    # HE QUITADO 'vol.' y 'no.' DE ESTA LISTA PARA ARREGLAR EL ERROR DE BOGACHEV
    palabras_clave = ['revista', 'journal', 'doi.org', 'issn', 'transactions', 'proceedings']
    return any(p in t for p in palabras_clave)

def cargar_archivo(uploaded_file):
    """Lectura blindada V38"""
    if uploaded_file is None: return None
    try:
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='utf-8')
    except:
        pass
    try:
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, sep=None, engine='python', encoding='latin-1')
    except:
        pass
    try:
        uploaded_file.seek(0)
        return pd.read_excel(uploaded_file, engine='openpyxl')
    except Exception as e:
        st.error(f"Error leyendo archivo: {e}")
        return None

@st.cache_data
def procesar_datos(file_ref, file_cat):
    df_ref = cargar_archivo(file_ref)
    df_cat = cargar_archivo(file_cat)

    if df_ref is None or df_cat is None: return pd.DataFrame()

    # Normalizar nombres columnas
    df_cat.columns = df_cat.columns.str.lower().str.strip()
    df_ref.columns = df_ref.columns.str.lower().str.strip()

    # Detectar columnas
    try:
        col_ref = [c for c in df_ref.columns if 'ref' in c or 'bib' in c][0]
        col_tit = [c for c in df_cat.columns if 'tit' in c][0]
        col_aut = [c for c in df_cat.columns if 'aut' in c][0]
    except:
        st.error("Error: No se detectaron columnas clave (Referencias, Titulo, Autor).")
        return pd.DataFrame()
    
    col_stock = next((c for c in df_cat.columns if any(x in c for x in ['ejem', 'copia', 'stock', 'cant'])), None)

    # Indexar Catálogo
    df_cat['busqueda'] = df_cat[col_tit].fillna('') + " " + df_cat[col_aut].fillna('')
    df_cat['busqueda_clean'] = df_cat['busqueda'].apply(limpiar_texto)

    if col_stock:
        df_cat[col_stock] = pd.to_numeric(df_cat[col_stock], errors='coerce').fillna(1)
        catalogo = df_cat.groupby('busqueda_clean')[col_stock].sum().to_dict()
        catalogo_nombres = df_cat.groupby('busqueda_clean')[col_tit].first().to_dict()
    else:
        catalogo = df_cat['busqueda_clean'].value_counts().to_dict()
        catalogo_nombres = df_cat.set_index('busqueda_clean')[col_tit].to_dict()

    lista_claves = list(catalogo.keys())
    
    resultados = []
    progress_bar = st.progress(0)
    total = len(df_ref)

    for idx, row in df_ref.iterrows():
        if idx % 10 == 0: progress_bar.progress(min(idx / total, 1.0))
        
        raw = str(row[col_ref])
        clean = limpiar_texto(raw)
        
        stock = 0
        estado = "NO ENCONTRADO"
        match_nom = ""
        tipo = "Libro"
        obs = ""
        
        # Variables cotización
        q_cotiz = re.sub(r'[^a-zA-Z0-9 ]', '', raw).replace(' ', '+')
        link_bf = f"https://www.bookfinder.com/search/?keywords={q_cotiz}&mode=basic&st=sr&ac=qr"
        link_bl = f"https://www.buscalibre.cl/libros/search?q={q_cotiz}"
        link_gg = f"https://www.google.com/search?tbm=bks&q={q_cotiz}"

        # 1. Detector de Artículos (CORREGIDO)
        if es_articulo_real(raw):
            tipo = "Artículo"
            estado = "VERIFICAR ONLINE"
            link_bf = f"https://scholar.google.com/scholar?q={q_cotiz}"
        
        # 2. Búsqueda de Libros
        elif len(clean) > 3:
            match = process.extractOne(clean, lista_claves, scorer=fuzz.token_set_ratio)
            if match:
                key, score, _ = match
                if score >= 70:
                    stock = int(catalogo[key])
                    match_nom = catalogo_nombres.get(key, "Match")
                    estado = "EN BIBLIOTECA" if stock > 0 else "FALTANTE (Stock 0)"
                    obs = f"Similitud: {int(score)}% ({match_nom})"
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
            "Link_BF": link_bf,
            "Link_BL": link_bl,
            "Link_GG": link_gg
        })
    
    progress_bar.progress(100)
    return pd.DataFrame(resultados)

# --- INTERFAZ ---
c1, c2 = st.columns(2)
file_ref = c1.file_uploader("1. Referencias", type=['csv', 'xlsx'])
file_cat = c2.file_uploader("2. Catálogo", type=['csv', 'xlsx'])

if file_ref and file_cat:
    if st.button("🚀 PROCESAR", type="primary"):
        with st.spinner('Analizando biblioteca...'):
            df = procesar_datos(file_ref, file_cat)
        
        # Métricas
        m1, m2, m3 = st.columns(3)
        m1.metric("Total", len(df))
        m2.metric("En Biblioteca", len(df[df['Stock'] > 0]))
        faltantes = df[(df['Stock'] == 0) & (df['Tipo'] == 'Libro')]
        m3.metric("Faltantes (A cotizar)", len(faltantes))

        # --- COTIZADOR VISUAL ---
        st.divider()
        st.subheader(f"🛒 Lista de Faltantes ({len(faltantes)})")
        
        if not faltantes.empty:
            for index, row in faltantes.iterrows():
                ref_text = row['Referencia'][:120] + "..." if len(row['Referencia']) > 120 else row['Referencia']
                
                col_text, col_btns = st.columns([3, 2])
                with col_text:
                    st.write(f"**{ref_text}**")
                with col_btns:
                    st.markdown(f"""
                        <a href="{row['Link_BF']}" target="_blank" class="cot-btn bf">BookFinder</a>
                        <a href="{row['Link_BL']}" target="_blank" class="cot-btn bl">Buscalibre</a>
                        <a href="{row['Link_GG']}" target="_blank" class="cot-btn gg">Google</a>
                    """, unsafe_allow_html=True)
                st.divider()
        else:
            st.info("¡Todo encontrado! No hay libros para cotizar.")

        # Excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False)
        
        st.download_button("📥 Descargar Excel Completo", buffer, "Resultado_Final_V40.xlsx")
