import streamlit as st
import pandas as pd
import re
from rapidfuzz import process, fuzz
import io
import csv

# --- CONFIGURACIÓN VISUAL ---
st.set_page_config(page_title="Gestor Bibliotecario V54", page_icon="🏛️", layout="wide")

st.markdown("""
<style>
    .stButton button { width: 100%; background-color: #2e86de; color: white; }
    .report-box { padding: 15px; border-radius: 10px; background-color: #f1f2f6; border: 1px solid #ced6e0; }
    .alert-box { color: #c0392b; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

st.title("🏛️ Gestor Bibliotecario V54 (Arquitectura Robusta)")
st.markdown("Reformulación completa: **Lector manual de archivos** y **Acumulador de Stock** para evitar sobrescritura de datos.")

# --- 1. MÓDULO DE LECTURA MANUAL (ELIMINA EL ERROR DE 191 FILAS) ---

def leer_referencias_raw(uploaded_file):
    """
    Lee el archivo como texto plano para arreglar saltos de línea internos
    que corrompen los CSVs generados por Excel.
    """
    try:
        # Decodificar bytes a string
        content = uploaded_file.getvalue().decode("latin-1") # Latin-1 es estándar en Chile/Windows
        lines = content.splitlines()
        
        data = []
        buffer = ""
        
        # Reconstruir líneas rotas (Heurística: Si la línea no termina en ";", es continuación)
        # Asumimos que tus referencias están en la primera columna y terminan con ;;;;;
        for line in lines:
            line = line.strip()
            if not line: continue # Saltar vacíos
            
            # Si la línea parece un encabezado, la saltamos o la guardamos
            if "Referencia" in line and "Unidad" in line: continue
            
            # Lógica de reconstrucción simple para tu formato específico
            # Si la línea es muy corta o no tiene separadores, la unimos a la anterior
            if len(line) < 10 or line.count(';') == 0:
                buffer += " " + line
            else:
                if buffer:
                    data.append(buffer)
                buffer = line
        
        if buffer: data.append(buffer) # Agregar el último
        
        # Convertir a DataFrame de 1 columna
        # Limpiamos los punto y coma extra del final
        clean_data = [row.split(';')[0] for row in data if len(row) > 5]
        
        return pd.DataFrame(clean_data, columns=["Referencias"])

    except Exception as e:
        st.error(f"Error en lectura manual: {e}")
        return pd.DataFrame()

def leer_catalogo_pandas(uploaded_file):
    """El catálogo suele estar mejor estructurado, usamos Pandas con motor robusto"""
    try:
        uploaded_file.seek(0)
        return pd.read_csv(uploaded_file, sep=';', encoding='latin-1', on_bad_lines='skip')
    except:
        try:
            uploaded_file.seek(0)
            return pd.read_excel(uploaded_file)
        except:
            return None

# --- 2. MÓDULO DE LIMPIEZA Y NORMALIZACIÓN ---

def limpiar_texto(texto):
    if pd.isna(texto): return ""
    t = str(texto).lower()
    # Quitar basura común
    t = t.replace('“', '').replace('”', '').replace('"', '').replace("'", "")
    t = t.replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    # Mantener solo letras y espacios para el match (quitamos números para el match de título)
    t_match = re.sub(r'[^a-z\s]', ' ', t)
    return " ".join(t_match.split())

def extraer_anio(texto):
    """Busca años 19xx o 20xx"""
    if pd.isna(texto): return "S/F"
    match = re.search(r'\b(19|20)\d{2}\b', str(texto))
    return int(match.group(0)) if match else 0

def generar_tokens(texto):
    return set(limpiar_texto(texto).split())

# --- 3. MÓDULO DE MATCH Y ACUMULACIÓN ---

@st.cache_data
def procesar_bibliografia(file_ref, file_cat):
    # 1. Carga
    df_ref = leer_referencias_raw(file_ref)
    df_cat = leer_catalogo_pandas(file_cat)
    
    if df_ref.empty or df_cat is None: return None

    # Normalizar encabezados catálogo
    df_cat.columns = df_cat.columns.astype(str).str.lower().str.strip()
    
    # Detectar columnas catálogo
    try:
        col_tit = [c for c in df_cat.columns if 'tit' in c][0]
        col_aut = [c for c in df_cat.columns if 'aut' in c][0]
        col_stock = [c for c in df_cat.columns if 'ejem' in c or 'copia' in c][0]
        col_anio = [c for c in df_cat.columns if 'fecha' in c or 'año' in c or 'year' in c][0]
    except:
        st.error("Error: El catálogo debe tener columnas: Titulo, Autor, Ejemplares, Fecha.")
        return None

    # --- FASE A: INDEXACIÓN DEL CATÁLOGO (EL ACUMULADOR) ---
    # Aquí solucionamos el problema de "1 vs 6". Sumamos todo.
    
    catalogo_index = {} # Clave: TokenHash -> Valor: Objeto Libro
    
    for idx, row in df_cat.iterrows():
        titulo = str(row[col_tit])
        autor = str(row[col_aut]) if pd.notna(row[col_aut]) else ""
        
        try: stock = int(row[col_stock])
        except: stock = 1
        
        try: anio = int(row[col_anio])
        except: anio = extraer_anio(str(row[col_anio])) # Intento secundario si la columna fecha está sucia

        # Creamos una clave única simplificada para agrupar ediciones
        # Usamos las primeras 2 palabras del título + 1 del autor
        t_clean = limpiar_texto(titulo)
        a_clean = limpiar_texto(autor)
        
        if len(t_clean) < 3: continue # Ignorar filas vacías
        
        # Clave de agrupación (Simplificada para agrupar "Chemical Process" 1984 y 2006)
        clave_agrupacion = t_clean 
        
        if clave_agrupacion not in catalogo_index:
            catalogo_index[clave_agrupacion] = {
                'titulo_oficial': titulo,
                'autor_oficial': autor,
                'stock_total': 0,
                'detalles_anios': [], # Lista de (Año, Cantidad)
                'tokens_titulo': generar_tokens(titulo),
                'tokens_autor': generar_tokens(autor)
            }
        
        # ACUMULAR (La clave del éxito)
        catalogo_index[clave_agrupacion]['stock_total'] += stock
        catalogo_index[clave_agrupacion]['detalles_anios'].append(f"{anio} ({stock})")

    # Preparar lista para RapidFuzz
    claves_catalogo = list(catalogo_index.keys())
    
    # --- FASE B: CRUCE DE REFERENCIAS ---
    resultados = []
    progreso = st.progress(0)
    total_refs = len(df_ref)
    
    for i, row in df_ref.iterrows():
        progreso.progress(min((i+1)/total_refs, 1.0))
        
        raw_ref = str(row["Referencias"])
        if len(raw_ref) < 5: continue # Saltar basura
        
        clean_ref = limpiar_texto(raw_ref)
        ref_tokens = generar_tokens(raw_ref)
        ref_anio = extraer_anio(raw_ref)
        
        # 1. Búsqueda Difusa
        match = process.extractOne(clean_ref, claves_catalogo, scorer=fuzz.token_set_ratio)
        
        estado = "NO ENCONTRADO"
        stock_encontrado = 0
        detalle_match = ""
        info_extra = ""
        link_cotizar = ""
        
        if match:
            clave_encontrada, puntaje, _ = match
            libro_cat = catalogo_index[clave_encontrada]
            
            # 2. Validación Estricta (Título + Autor)
            # El título ya hizo match difuso (puntaje). Ahora validamos autor.
            
            autor_coincide = False
            if not libro_cat['tokens_autor']: 
                autor_coincide = True # Si catalogo no tiene autor, confiamos en titulo
            else:
                # Intersección de tokens de autor
                interseccion = libro_cat['tokens_autor'].intersection(ref_tokens)
                if len(interseccion) > 0: autor_coincide = True
            
            # REGLA MAESTRA V54
            if puntaje >= 80 and autor_coincide:
                stock_encontrado = libro_cat['stock_total']
                
                # Análisis de Años
                anios_str = ", ".join(libro_cat['detalles_anios'])
                
                if stock_encontrado > 0:
                    estado = "EN BIBLIOTECA"
                    detalle_match = libro_cat['titulo_oficial']
                    info_extra = f"Total Copias: {stock_encontrado} | Desglose: {anios_str}"
                    
                    # Alerta de antigüedad (Opcional)
                    if ref_anio > 0:
                        # Verificar si tenemos algun año igual o superior
                        anios_nums = [int(re.search(r'\d+', x).group()) for x in libro_cat['detalles_anios'] if re.search(r'\d+', x)]
                        if anios_nums and max(anios_nums) < ref_anio:
                            estado = "EN BIBLIOTECA (Desactualizado)"
                            info_extra += " ⚠️ Todas las copias son más antiguas que la referencia."
                else:
                    estado = "FALTANTE (Stock 0)"
            else:
                estado = "FALTANTE"
                q = re.sub(r'[^a-zA-Z0-9 ]', '', raw_ref).replace(" ", "+")
                link_cotizar = f"https://www.buscalibre.cl/libros/search?q={q}"

        resultados.append({
            "Referencia Original": raw_ref,
            "Estado": estado,
            "Stock Total": stock_encontrado,
            "Detalle Existencias": info_extra,
            "Match Título": detalle_match,
            "Link Buscalibre": link_cotizar
        })
        
    return pd.DataFrame(resultados)

# --- INTERFAZ DE USUARIO ---

c1, c2 = st.columns(2)
archivo_ref = c1.file_uploader("1. Sube Referencias (Excel/CSV)", type=['csv','xlsx'])
archivo_cat = c2.file_uploader("2. Sube Catálogo (Excel/CSV)", type=['csv','xlsx'])

if archivo_ref and archivo_cat:
    if st.button("🔍 AUDITAR BIBLIOTECA", type="primary"):
        with st.spinner("Reconstruyendo base de datos..."):
            df_final = procesar_bibliografia(archivo_ref, archivo_cat)
        
        if df_final is not None:
            # Métricas Reales
            total = len(df_final)
            encontrados = len(df_final[df_final['Stock Total'] > 0])
            faltantes = total - encontrados
            
            st.success("Análisis Finalizado")
            
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Referencias Leídas", total, help="Si este número no es ~93, revisa tu archivo.")
            col_b.metric("Encontrados", encontrados)
            col_c.metric("Faltantes", faltantes)
            
            # Tabla interactiva
            st.dataframe(df_final)
            
            # Descarga
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, index=False)
            
            st.download_button("📥 Descargar Reporte Detallado", buffer, "Reporte_Biblioteca_V54.xlsx")
