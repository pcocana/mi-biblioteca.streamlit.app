import streamlit as st
import pandas as pd
import re
from rapidfuzz import process, fuzz
import io
import unicodedata

# ==========================================
# CONFIGURACIÓN DE LA PÁGINA
# ==========================================
st.set_page_config(
    page_title="Gestor Bibliotecario AI",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================
# CONFIGURACIÓN AJUSTABLE (SIDEBAR)
# ==========================================
with st.sidebar:
    st.header("⚙️ Configuración")
    
    UMBRAL_SIMILITUD = st.slider(
        "Umbral de similitud (%)",
        min_value=50,
        max_value=95,
        value=70,
        step=5,
        help="Porcentaje mínimo de similitud para considerar un match válido"
    )
    
    MOSTRAR_DEBUG = st.checkbox("Mostrar info de depuración", value=False)
    
    st.markdown("---")
    st.markdown("""
    ### 📖 Cómo usar:
    1. Sube tu archivo de **Referencias** (bibliografía)
    2. Sube tu archivo de **Catálogo** (libros disponibles)
    3. Presiona **Procesar**
    4. Descarga el resultado
    
    ### 📌 Formatos aceptados:
    - Excel (.xlsx, .xls)
    - CSV (.csv)
    
    ### 🔍 El sistema detecta:
    - Libros en stock
    - Libros sin stock
    - Artículos científicos
    - Referencias a cotizar
    """)

# ==========================================
# FUNCIONES AUXILIARES
# ==========================================

def normalizar_texto(texto):
    """Normaliza texto eliminando acentos y caracteres especiales"""
    if pd.isna(texto):
        return ""
    
    # Convertir a string y normalizar Unicode
    texto = str(texto)
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join([c for c in texto if not unicodedata.combining(c)])
    
    return texto.lower()

def limpiar_texto(texto):
    """Limpieza profunda: quita URLs, años y caracteres raros"""
    if pd.isna(texto):
        return ""
    
    t = normalizar_texto(texto)
    
    # Quitar URLs
    t = re.sub(r'http\S+|www\.\S+', '', t)
    
    # Quitar años entre paréntesis (2020)
    t = re.sub(r'\(\d{4}\)', '', t)
    
    # Quitar caracteres no alfanuméricos
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    
    # Colapsar espacios múltiples
    return " ".join(t.split())

def es_articulo_cientifico(texto):
    """Detecta si la referencia es un artículo científico"""
    t = str(texto).lower()
    
    indicadores = [
        'revista', 'journal', 'doi.org', 'issn', 
        'transactions', 'proceedings', 'vol.', 'no.',
        'pp.', 'issue', 'quarterly', 'annual'
    ]
    
    return any(indicador in t for indicador in indicadores)

def detectar_columna(df, posibles_nombres, nombre_tipo="columna"):
    """
    Detecta columna de forma inteligente con manejo de errores
    
    Args:
        df: DataFrame
        posibles_nombres: Lista de strings para buscar en nombres de columnas
        nombre_tipo: Nombre descriptivo para mensajes de error
    
    Returns:
        str: Nombre de la columna encontrada
    
    Raises:
        ValueError: Si no encuentra ninguna columna válida
    """
    columnas_encontradas = [
        col for col in df.columns 
        if any(nombre in col for nombre in posibles_nombres)
    ]
    
    if not columnas_encontradas:
        raise ValueError(
            f"No se encontró {nombre_tipo}. "
            f"Se esperaba alguna de: {', '.join(posibles_nombres)}. "
            f"Columnas disponibles: {', '.join(df.columns)}"
        )
    
    return columnas_encontradas[0]

def cargar_archivo(uploaded_file):
    """Carga archivo Excel o CSV con manejo robusto de errores"""
    try:
        if uploaded_file.name.endswith('.csv'):
            # Intentar diferentes encodings
            try:
                df = pd.read_csv(uploaded_file, encoding='utf-8')
            except UnicodeDecodeError:
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, encoding='latin-1')
        else:
            df = pd.read_excel(uploaded_file)
        
        # Normalizar nombres de columnas
        df.columns = df.columns.str.lower().str.strip()
        
        return df, None
        
    except Exception as e:
        return None, f"Error al leer archivo: {str(e)}"

# ==========================================
# FUNCIÓN PRINCIPAL DE PROCESAMIENTO
# ==========================================

def procesar_referencias(df_ref, df_cat, umbral):
    """
    Procesa referencias contra catálogo
    
    Args:
        df_ref: DataFrame de referencias
        df_cat: DataFrame de catálogo
        umbral: Umbral de similitud (0-100)
    
    Returns:
        DataFrame con resultados
    """
    
    # 1. DETECTAR COLUMNAS CLAVE
    try:
        col_ref = detectar_columna(
            df_ref, 
            ['ref', 'bib', 'titulo', 'title', 'citation'],
            "columna de referencias"
        )
        
        col_tit = detectar_columna(
            df_cat,
            ['tit', 'title', 'nombre', 'libro'],
            "columna de título"
        )
        
        col_aut = detectar_columna(
            df_cat,
            ['aut', 'author', 'autor', 'escritor'],
            "columna de autor"
        )
        
        # Stock es opcional
        try:
            col_stock = detectar_columna(
                df_cat,
                ['ejem', 'copia', 'stock', 'cant', 'disponible'],
                "columna de stock"
            )
        except ValueError:
            col_stock = None
            st.warning("⚠️ No se detectó columna de stock. Se asumirá stock = 1 para todos los libros.")
        
    except ValueError as e:
        st.error(f"❌ Error en estructura de archivos: {str(e)}")
        return None
    
    # 2. PREPARAR CATÁLOGO
    st.info(f"📊 Columnas detectadas - Referencias: `{col_ref}` | Catálogo: `{col_tit}`, `{col_aut}`" + 
            (f", `{col_stock}`" if col_stock else ""))
    
    # Crear campo de búsqueda combinado
    df_cat['busqueda'] = (
        df_cat[col_tit].fillna('') + " " + 
        df_cat[col_aut].fillna('')
    )
    df_cat['busqueda_clean'] = df_cat['busqueda'].apply(limpiar_texto)
    
    # Crear diccionarios de búsqueda
    if col_stock:
        df_cat[col_stock] = pd.to_numeric(df_cat[col_stock], errors='coerce').fillna(1)
        catalogo_stock = df_cat.groupby('busqueda_clean')[col_stock].sum().to_dict()
    else:
        catalogo_stock = {key: 1 for key in df_cat['busqueda_clean'].unique()}
    
    catalogo_nombres = df_cat.groupby('busqueda_clean')[col_tit].first().to_dict()
    catalogo_autores = df_cat.groupby('busqueda_clean')[col_aut].first().to_dict()
    
    lista_claves = list(catalogo_stock.keys())
    
    # 3. PROCESAR REFERENCIAS
    resultados = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total_refs = len(df_ref)
    
    for idx, row in df_ref.iterrows():
        # Actualizar progreso
        progreso = (idx + 1) / total_refs
        progress_bar.progress(progreso)
        status_text.text(f"Procesando referencia {idx + 1} de {total_refs}...")
        
        raw = str(row[col_ref])
        clean = limpiar_texto(raw)
        
        # Inicializar resultado
        resultado = {
            "Referencia Original": raw,
            "Estado": "NO ENCONTRADO",
            "Stock": 0,
            "Título Catálogo": "",
            "Autor Catálogo": "",
            "Tipo": "Libro",
            "Similitud": 0,
            "Link Cotización": "",
            "Observaciones": ""
        }
        
        # CASO 1: Detectar artículos científicos
        if es_articulo_cientifico(raw):
            resultado.update({
                "Tipo": "Artículo Científico",
                "Estado": "VERIFICAR ONLINE",
                "Observaciones": "Posible paper/revista científica",
                "Link Cotización": f"https://scholar.google.com/scholar?q={raw.replace(' ', '+')}"
            })
        
        # CASO 2: Buscar en catálogo
        elif len(clean) > 3:
            match = process.extractOne(
                clean, 
                lista_claves, 
                scorer=fuzz.token_set_ratio
            )
            
            if match:
                mejor_key, similitud, _ = match
                
                # MATCH ENCONTRADO
                if similitud >= umbral:
                    stock = int(catalogo_stock.get(mejor_key, 0))
                    titulo = catalogo_nombres.get(mejor_key, "")
                    autor = catalogo_autores.get(mejor_key, "")
                    
                    resultado.update({
                        "Estado": "EN BIBLIOTECA" if stock > 0 else "FALTANTE (Stock 0)",
                        "Stock": stock,
                        "Título Catálogo": titulo,
                        "Autor Catálogo": autor,
                        "Similitud": round(similitud),
                        "Observaciones": f"Match encontrado con {round(similitud)}% de similitud"
                    })
                
                # NO HAY MATCH SUFICIENTE
                else:
                    resultado.update({
                        "Estado": "COTIZAR",
                        "Similitud": round(similitud),
                        "Observaciones": f"Similitud insuficiente ({round(similitud)}% < {umbral}%)",
                        "Link Cotización": generar_link_cotizacion(raw)
                    })
        
        # CASO 3: Referencia muy corta
        else:
            resultado["Observaciones"] = "Referencia demasiado corta para analizar"
        
        resultados.append(resultado)
    
    progress_bar.empty()
    status_text.empty()
    
    return pd.DataFrame(resultados)

def generar_link_cotizacion(referencia):
    """Genera link de BookFinder para cotización"""
    # Limpiar texto para URL
    texto_limpio = re.sub(r'[^a-zA-Z0-9 ]', '', referencia)
    query = texto_limpio.replace(' ', '+')
    
    return f"https://www.bookfinder.com/search/?keywords={query}&mode=basic&st=sr&ac=qr"

# ==========================================
# INTERFAZ PRINCIPAL
# ==========================================

st.title("📚 Gestor Bibliotecario Inteligente")
st.markdown("""
Esta aplicación cruza automáticamente tu lista de **Referencias** con el **Catálogo**, 
detectando existencias reales, artículos científicos y corrigiendo errores de escritura.

---
""")

# Columnas para carga de archivos
col1, col2 = st.columns(2)

with col1:
    st.subheader("1️⃣ Cargar Referencias")
    uploaded_ref = st.file_uploader(
        "Sube archivo de Referencias (Excel/CSV)",
        type=['csv', 'xlsx', 'xls'],
        help="Archivo con la bibliografía a verificar"
    )
    
    if uploaded_ref:
        st.success(f"✅ Archivo cargado: {uploaded_ref.name}")

with col2:
    st.subheader("2️⃣ Cargar Catálogo")
    uploaded_cat = st.file_uploader(
        "Sube archivo de Catálogo (Excel/CSV)",
        type=['csv', 'xlsx', 'xls'],
        help="Archivo con los libros disponibles en biblioteca"
    )
    
    if uploaded_cat:
        st.success(f"✅ Archivo cargado: {uploaded_cat.name}")

# Botón de procesamiento
if uploaded_ref and uploaded_cat:
    
    st.markdown("---")
    
    if st.button("🚀 INICIAR PROCESAMIENTO", type="primary", use_container_width=True):
        
        # Cargar archivos
        with st.spinner('📖 Leyendo archivos...'):
            df_ref, error_ref = cargar_archivo(uploaded_ref)
            df_cat, error_cat = cargar_archivo(uploaded_cat)
        
        # Validar carga
        if error_ref:
            st.error(f"Error en archivo de referencias: {error_ref}")
        elif error_cat:
            st.error(f"Error en archivo de catálogo: {error_cat}")
        else:
            # Mostrar preview
            with st.expander("👀 Vista previa de datos cargados"):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.write("**Referencias:**")
                    st.dataframe(df_ref.head(3), use_container_width=True)
                with col_b:
                    st.write("**Catálogo:**")
                    st.dataframe(df_cat.head(3), use_container_width=True)
            
            # Procesar
            with st.spinner('🤖 El bibliotecario digital está trabajando...'):
                df_result = procesar_referencias(df_ref, df_cat, UMBRAL_SIMILITUD)
            
            if df_result is not None:
                st.success("✅ ¡Proceso Completado!")
                
                # MÉTRICAS
                st.markdown("### 📊 Resumen de Resultados")
                
                col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                
                total = len(df_result)
                en_biblio = len(df_result[df_result['Stock'] > 0])
                faltantes = len(df_result[df_result['Estado'] == 'FALTANTE (Stock 0)'])
                articulos = len(df_result[df_result['Tipo'] == 'Artículo Científico'])
                cotizar = len(df_result[df_result['Estado'] == 'COTIZAR'])
                
                col_m1.metric("📚 Total Referencias", total)
                col_m2.metric("✅ En Biblioteca", en_biblio, 
                             delta=f"{round(en_biblio/total*100)}%" if total > 0 else "0%")
                col_m3.metric("⚠️ Por Cotizar", cotizar)
                col_m4.metric("📄 Artículos", articulos)
                
                # TABLA DE RESULTADOS
                st.markdown("### 📋 Tabla de Resultados")
                
                # Filtros
                filtro_col1, filtro_col2 = st.columns(2)
                
                with filtro_col1:
                    filtro_estado = st.multiselect(
                        "Filtrar por Estado:",
                        options=df_result['Estado'].unique(),
                        default=df_result['Estado'].unique()
                    )
                
                with filtro_col2:
                    filtro_tipo = st.multiselect(
                        "Filtrar por Tipo:",
                        options=df_result['Tipo'].unique(),
                        default=df_result['Tipo'].unique()
                    )
                
                # Aplicar filtros
                df_filtrado = df_result[
                    (df_result['Estado'].isin(filtro_estado)) &
                    (df_result['Tipo'].isin(filtro_tipo))
                ]
                
                # Mostrar tabla
                st.dataframe(
                    df_filtrado,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Link Cotización": st.column_config.LinkColumn("🔗 Cotizar"),
                        "Stock": st.column_config.NumberColumn("📦 Stock", format="%d"),
                        "Similitud": st.column_config.NumberColumn("🎯 Similitud", format="%d%%")
                    }
                )
                
                # BOTÓN DE DESCARGA
                st.markdown("### 💾 Descargar Resultados")
                
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df_result.to_excel(writer, index=False, sheet_name='Resultados')
                    
                    # Formato mejorado
                    workbook = writer.book
                    worksheet = writer.sheets['Resultados']
                    
                    # Formatos
                    header_format = workbook.add_format({
                        'bold': True,
                        'bg_color': '#4472C4',
                        'font_color': 'white',
                        'border': 1
                    })
                    
                    link_format = workbook.add_format({
                        'font_color': 'blue',
                        'underline': 1
                    })
                    
                    # Aplicar formato a encabezados
                    for col_num, value in enumerate(df_result.columns.values):
                        worksheet.write(0, col_num, value, header_format)
                    
                    # Ajustar anchos de columna
                    worksheet.set_column('A:A', 50)  # Referencia
                    worksheet.set_column('B:B', 20)  # Estado
                    worksheet.set_column('C:C', 8)   # Stock
                    worksheet.set_column('D:D', 35)  # Título
                    worksheet.set_column('E:E', 25)  # Autor
                    worksheet.set_column('F:F', 15)  # Tipo
                    worksheet.set_column('G:G', 10)  # Similitud
                    worksheet.set_column('H:H', 40)  # Link
                    worksheet.set_column('I:I', 40)  # Observaciones
                    
                    # Aplicar formato de link
                    for row_num, url in enumerate(df_result['Link Cotización'], start=1):
                        if url:
                            worksheet.write_url(row_num, 7, url, link_format, string="Cotizar")
                
                st.download_button(
                    label="📥 Descargar Excel Completo",
                    data=buffer.getvalue(),
                    file_name=f"Resultados_Biblioteca_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
                
                # INFO DE DEBUG
                if MOSTRAR_DEBUG:
                    with st.expander("🔧 Información de Depuración"):
                        st.write("**Estadísticas del procesamiento:**")
                        st.json({
                            "Total referencias": total,
                            "En biblioteca": en_biblio,
                            "Faltantes": faltantes,
                            "Artículos": articulos,
                            "Por cotizar": cotizar,
                            "Umbral usado": UMBRAL_SIMILITUD,
                            "Columnas detectadas": {
                                "Referencias": list(df_ref.columns),
                                "Catálogo": list(df_cat.columns)
                            }
                        })

else:
    st.info("👆 Por favor, carga ambos archivos para comenzar el procesamiento.")

# Footer
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666;'>
    <p>Gestor Bibliotecario Inteligente | Versión 2.0 | 
    Desarrollado con ❤️ usando Streamlit</p>
</div>
""", unsafe_allow_html=True)
