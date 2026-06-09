import os
import time
import shutil
import tempfile
import logging
from dotenv import load_dotenv
import gspread
import gestor_almacenamiento
import preprocesar_audio
import motor_transcripcion

# Cargar las variables de entorno desde el archivo .env al inicio
load_dotenv()

# Configuración de logging para verlo en consola local
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- VARIABLES DINÁMICAS DESDE .ENV ---
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
FOLDER_DOCS_ID = os.getenv("FOLDER_DOCS_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "8x8 Call Records") # Valor por defecto por si se te olvida ponerlo
COL_INPUT_LETTER = os.getenv("COL_INPUT", "Q")           # Columna donde buscará los enlaces
COL_OUTPUT_LETTER = os.getenv("COL_OUTPUT", "R")         # Columna donde escribirá el Google Doc

def letra_a_columna(letra: str) -> int:
    """
    Convierte una letra de columna de Excel/Sheets (ej. 'A', 'Q', 'AA') 
    a un índice numérico base 1 (ej. A=1, Q=17, R=18).
    """
    letra = letra.upper()
    columna = 0
    for char in letra:
        columna = columna * 26 + (ord(char) - ord('A')) + 1
    return columna

def obtener_hoja():
    """Autentica y obtiene la hoja de cálculo usando las credenciales de OAuth locales."""
    creds = gestor_almacenamiento.obtener_credenciales_oauth()
    cliente_sheets = gspread.authorize(creds)
    spreadsheet = cliente_sheets.open_by_key(SPREADSHEET_ID)
    # Ahora usamos la variable dinámica para el nombre de la pestaña
    return spreadsheet.worksheet(SHEET_NAME)

def procesar_fila(hoja, num_fila: int, enlace_audio: str, col_output_idx: int):
    """Ejecuta el flujo completo para un enlace y actualiza la hoja."""
    logger.info(f"--- Iniciando procesamiento para la fila {num_fila} ---")
    
    carpeta_trabajo = tempfile.mkdtemp(prefix=f"transcripcion_fila_{num_fila}_")
    carpeta_segmentos = os.path.join(carpeta_trabajo, "segmentos")
    os.makedirs(carpeta_segmentos, exist_ok=True)
    
    try:
        # 1. Descargar el audio
        logger.info(f"Paso 1: Descargando audio desde -> {enlace_audio}")
        ruta_audio = gestor_almacenamiento.descargar_audio(enlace_audio, carpeta_trabajo)
        
        # 2. Preprocesar (FFMPEG)
        logger.info("Paso 2: Preprocesando y segmentando con FFMPEG...")
        rutas_segmentos = preprocesar_audio.preprocesar_con_ffmpeg(ruta_audio, carpeta_segmentos)
        
        # 3. Transcribir y Analizar (Vertex AI)
        logger.info("Paso 3: Transcribiendo y analizando con Vertex AI...")
        datos_procesados = motor_transcripcion.procesar_transcripcion_completa(carpeta_segmentos)
        
        # 4. Generar Google Doc
        logger.info("Paso 4: Generando el Google Doc...")
        texto_doc = motor_transcripcion.generar_texto_para_doc(datos_procesados)
        titulo_doc = f"Transcripción y Análisis - Fila {num_fila}"
        url_doc = gestor_almacenamiento.crear_google_doc_con_transcripcion(titulo_doc, texto_doc, FOLDER_DOCS_ID)
        
        # 5. Escribir resultado en la columna dinámica
        logger.info(f"Paso 5: Actualizando Google Sheet en la fila {num_fila}, columna {COL_OUTPUT_LETTER}...")
        hoja.update_cell(num_fila, col_output_idx, url_doc)
        logger.info(f"✅ Fila {num_fila} completada con éxito.")

    except Exception as e:
        error_msg = f"ERROR: {str(e)}"
        logger.error(f"❌ Fallo en la fila {num_fila}: {error_msg}")
        hoja.update_cell(num_fila, col_output_idx, error_msg)

    finally:
        shutil.rmtree(carpeta_trabajo, ignore_errors=True)
        logger.info(f"Recursos temporales liberados para fila {num_fila}.\n")

def ejecutar_pipeline():
    """Lee la hoja, busca audios pendientes y los procesa uno por uno."""
    logger.info(f"Conectando a Google Sheets (Pestaña: {SHEET_NAME})...")
    try:
        if not SPREADSHEET_ID or not FOLDER_DOCS_ID:
            raise ValueError("Falta SPREADSHEET_ID o FOLDER_DOCS_ID en tu archivo .env")

        hoja = obtener_hoja()
        valores = hoja.get_all_values()
        
        # Calculamos los índices numéricos a partir de las letras
        col_input_idx_base1 = letra_a_columna(COL_INPUT_LETTER)
        col_output_idx_base1 = letra_a_columna(COL_OUTPUT_LETTER)
        
        # Los arrays en Python empiezan en 0, por lo que restamos 1 para leer de 'valores'
        indice_array_input = col_input_idx_base1 - 1
        indice_array_output = col_output_idx_base1 - 1

        # Iteramos desde la fila 5 (índice 4 en programación)
        for idx in range(4, len(valores)):
            fila = valores[idx]
            num_fila_real = idx + 1 
            
            # Validamos que la fila no esté vacía y que tenga suficientes columnas
            enlace_q = fila[indice_array_input].strip() if len(fila) > indice_array_input else ""
            resultado_r = fila[indice_array_output].strip() if len(fila) > indice_array_output else ""
            
            # Si hay enlace y el resultado está vacío
            if enlace_q and not resultado_r:
                procesar_fila(hoja, num_fila_real, enlace_q, col_output_idx_base1)
                
        logger.info("Se ha barrido toda la hoja y no hay más casos pendientes.")

    except Exception as e:
        logger.error(f"Error crítico en el orquestador: {e}")

if __name__ == "__main__":
    print("\n" + "="*50)
    print("INICIANDO TRANSCRIPTOR BATCH (LOCAL)")
    print("="*50 + "\n")
    ejecutar_pipeline()