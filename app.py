import streamlit as st
import pandas as pd
import re
from rapidfuzz import process, fuzz
import io

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor Bibliotecario AI", page_icon="📚", layout="wide")

st.title("📚 Gestor Bibliotecario Inteligente V36")
st.markdown("""
Esta aplicación cruza automáticamente tu lista de **Referencias** con el **Catálogo**, 
detectando existencias reales, artículos científicos y corrigiendo errores de escritura.
""")

# --- FUNCIONES DE LÓGICA (EL CEREBRO PYTHON) ---

def limpiar_texto(texto):
    """Limpieza profunda: quita URLs, años y caracteres raros"""
    if pd.isna(texto): return ""
    t = str(texto).lower()
    t = re.sub(r'http\S+|www\.\S+', '', t) # Quitar URLs
    t = re.sub(r'\(\d{4}\)', '', t) # Quitar años (2020)
    t = t.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    return " ".join(t.split())

def es_articulo_real(texto):
    t = str(texto).lower()
    palabras_clave = ['revista', 'journal', 'doi.org', 'issn', 'transactions', 'proceedings', 'vol.', 'no.']
    return any(p in t for p in palabras_clave)

@st.cache_data
def procesar_datos(file_ref, file_cat):
    # 1. LEER ARCHIVOS (Detectando formato)
    try:
        df_ref = pd.read_csv(file_ref, sep=None, engine='python', encoding='utf-8')
    except:
        df_ref = pd.read_excel(file_ref)

    try:
        df_cat = pd.read_csv(file_cat, sep=None, engine='python', encoding='utf-8')
    except:
        df_cat = pd.read_excel(file_cat)

    # Normalizar columnas
    df_cat.columns = df_cat.columns.str.lower().str.strip()
    df_ref.columns = df_ref.columns.str.lower().str.strip()

    # Detectar columnas clave
    col_ref = [c for c in df_ref.columns if 'ref' in c or 'bib' in c][0]
    col_tit = [c for c in df_cat.columns if 'tit' in c][0]
    col_aut = [c for c in df_cat.columns if 'aut' in c][0]
    
    # Stock: buscar 'ejemplar', 'copia', 'stock'
    posibles_stock = [c for c in df_cat.columns if any(x in c for x in ['ejem', 'copia', 'stock', 'cant'])]
    col_stock = posibles_stock[0] if posibles_stock else None

    # Crear "Diccionario Maestro" del Catálogo
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
    
    # Procesar Referencias
    resultados = []
    
    # Barra de progreso
    progress_bar = st.progress(0)
    total_rows = len(df_ref)

    for idx, row in df_ref.iterrows():
        # Actualizar barra cada 10 items
        if idx % 10 == 0: progress_bar.progress(min(idx / total_rows, 1.0))

        raw = str(row[col_ref])
        clean = limpiar_texto(raw)
        
        stock_encontrado = 0
        estado = "NO ENCONTRADO"
        match_nombre = ""
        tipo = "Libro"
        url_cotiz = ""
        obs = ""

        if es_articulo_real(raw):
            tipo = "Artículo"
            estado = "VERIFICAR ONLINE"
            obs = "Posible paper/revista"
            url_cotiz = f"https://scholar.google.com/scholar?q={raw}"
        
        elif len(clean) > 3:
            # EL ALGORITMO MÁGICO (Rapidfuzz)
            match = process.extractOne(clean, lista_claves, scorer=fuzz.token_set_ratio)
            
            if match:
                mejor_key, puntaje, _ = match
                
                if puntaje >= 70: # Umbral V35
                    stock_encontrado = int(catalogo[mejor_key])
                    match_nombre = catalogo_nombres.get(mejor_key, "Match encontrado")
                    estado = "EN BIBLIOTECA" if stock_encontrado > 0 else "FALTANTE (Stock 0)"
                    obs = f"Similitud: {round(puntaje)}% (Match: {match_nombre})"
                else:
                    estado = "FALTANTE"
                    obs = "Sin coincidencia suficiente"
                    # Link limpio para cotizar
                    q = re.sub(r'[^a-zA-Z0-9 ]', '', raw)
                    url_cotiz = f"https://www.bookfinder.com/search/?keywords={q.replace(' ', '+')}&mode=basic&st=sr&ac=qr"

        resultados.append({
            "Referencia Original": raw,
            "Estado": estado,
            "Stock": stock_encontrado,
            "Match Catálogo": match_nombre,
            "Tipo": tipo,
            "Link Cotización": url_cotiz,
            "Observaciones": obs
        })
    
    progress_bar.progress(100)
    return pd.DataFrame(resultados)

# --- INTERFAZ GRÁFICA ---

col1, col2 = st.columns(2)

with col1:
    st.subheader("1. Cargar Referencias")
    uploaded_ref = st.file_uploader("Sube archivo de Referencias (Excel/CSV)", type=['csv', 'xlsx'])

with col2:
    st.subheader("2. Cargar Catálogo")
    uploaded_cat = st.file_uploader("Sube archivo de Catálogo (Excel/CSV)", type=['csv', 'xlsx'])

if uploaded_ref and uploaded_cat:
    if st.button("🚀 INICIAR PROCESAMIENTO", type="primary"):
        with st.spinner('El bibliotecario digital está trabajando...'):
            df_result = procesar_datos(uploaded_ref, uploaded_cat)
        
        st.success("¡Proceso Completado!")
        
        # Métricas rápidas
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Referencias", len(df_result))
        c2.metric("En Biblioteca", len(df_result[df_result['Stock'] > 0]))
        c3.metric("Faltantes", len(df_result[df_result['Stock'] == 0]))

        st.dataframe(df_result)

        # Botón de descarga
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df_result.to_excel(writer, index=False, sheet_name='Resultados')
            # Formato de links
            workbook = writer.book
            worksheet = writer.sheets['Resultados']
            link_fmt = workbook.add_format({'font_color': 'blue', 'underline': 1})
            
            # Aplicar formato link a la columna F (índice 5)
            for i, url in enumerate(df_result['Link Cotización']):
                if url: worksheet.write_url(i+1, 5, url, link_fmt, string="Cotizar")

        st.download_button(
            label="📥 Descargar Excel Final",
            data=buffer,
            file_name="Planilla_Bibliotecaria_Final.xlsx",
            mime="application/vnd.ms-excel"
        )