import os
import re
import time
import logging
from dotenv import load_dotenv
import gspread
import vertexai
from vertexai.generative_models import GenerativeModel, HarmCategory, HarmBlockThreshold
import gestor_almacenamiento

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CONFIGURACIÓN DESDE .ENV ---
PROJECT_ID        = os.getenv("PROJECT_ID")
LOCATION          = os.getenv("LOCATION", "global")
SPREADSHEET_ID    = os.getenv("SPREADSHEET_ID")
SHEET_NAME        = os.getenv("SHEET_NAME", "Hoja 1")
FOLDER_DOCS_ID    = os.getenv("FOLDER_DOCS_ID")

# COL_OUTPUT tiene el enlace al doc de transcripción (entrada de este motor)
# COL_ENTREGABLE es donde escribimos el enlace al informe de análisis (salida)
COL_FUENTE_LETTER   = os.getenv("COL_OUTPUT", "E")
COL_DESTINO_LETTER  = os.getenv("COL_ENTREGABLE", "F")

PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompt_analisis.txt")
MODEL_ID    = "gemini-3.5-flash"


# --- UTILIDADES ---
def letra_a_columna(letra: str) -> int:
    letra = letra.upper()
    col = 0
    for c in letra:
        col = col * 26 + (ord(c) - ord('A')) + 1
    return col


def extraer_id_documento(url: str) -> str:
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)
    raise ValueError(f"No se pudo extraer el ID de documento desde: {url}")


def leer_prompt() -> str:
    with open(PROMPT_FILE, 'r', encoding='utf-8') as f:
        return f.read()


def obtener_hoja():
    creds = gestor_almacenamiento.obtener_credenciales_oauth()
    cliente = gspread.authorize(creds)
    return cliente.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)


# --- NÚCLEO DEL ANÁLISIS ---
def analizar_con_vertex(texto_transcripcion: str, prompt_base: str) -> str:
    """Envía la transcripción al modelo de Vertex AI y devuelve el análisis en Markdown."""
    vertexai.init(project=PROJECT_ID, location=LOCATION)

    configuracion_segura = {
        HarmCategory.HARM_CATEGORY_HATE_SPEECH:       HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HARASSMENT:         HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT:  HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT:  HarmBlockThreshold.BLOCK_NONE,
    }

    # El prompt ya termina con "## TRANSCRIPCIÓN A ANALIZAR\n", se adjunta el texto a continuación
    prompt_completo = f"{prompt_base}\n{texto_transcripcion}"

    modelo = GenerativeModel(MODEL_ID)
    respuesta = modelo.generate_content(
        prompt_completo,
        safety_settings=configuracion_segura,
        generation_config={"max_output_tokens": 60000}
    )
    return respuesta.text


def procesar_fila_analisis(hoja, num_fila: int, url_transcripcion: str,
                           col_destino_idx: int, prompt_base: str):
    """Ejecuta el flujo completo de análisis para una fila y escribe el resultado en la hoja."""
    logger.info(f"--- Analizando fila {num_fila} ---")
    try:
        # 1. Leer el Google Doc de transcripción
        doc_id = extraer_id_documento(url_transcripcion)
        logger.info(f"Leyendo transcripción desde doc ID: {doc_id}")
        texto_transcripcion = gestor_almacenamiento.leer_contenido_google_doc(doc_id)
        logger.info(f"Transcripción obtenida: {len(texto_transcripcion)} caracteres.")

        if not texto_transcripcion.strip():
            raise ValueError("El Google Doc de transcripción está vacío.")

        # 2. Enviar a Vertex AI con el prompt de análisis
        logger.info("Enviando a Vertex AI para análisis de la llamada...")
        respuesta_markdown = analizar_con_vertex(texto_transcripcion, prompt_base)

        # 3. Crear Google Doc con formato basado en el Markdown devuelto
        titulo = f"Análisis de Llamada - Fila {num_fila}"
        logger.info(f"Generando Google Doc de análisis: '{titulo}'...")
        url_doc = gestor_almacenamiento.crear_google_doc_desde_markdown(
            titulo, respuesta_markdown, FOLDER_DOCS_ID
        )

        # 4. Escribir el enlace en COL_ENTREGABLE
        hoja.update_cell(num_fila, col_destino_idx, url_doc)
        logger.info(f"✅ Fila {num_fila} completada. Informe: {url_doc}")

    except Exception as e:
        error_msg = f"ERROR ANÁLISIS: {str(e)}"
        logger.error(f"❌ Fallo en análisis de fila {num_fila}: {error_msg}")
        hoja.update_cell(num_fila, col_destino_idx, error_msg)


# --- ORQUESTADOR PRINCIPAL ---
def ejecutar_analisis():
    """Recorre la hoja y analiza todas las filas pendientes (con transcripción pero sin informe)."""
    logger.info(f"Conectando a Google Sheets (Pestaña: {SHEET_NAME})...")

    if not SPREADSHEET_ID or not FOLDER_DOCS_ID:
        raise ValueError("Falta SPREADSHEET_ID o FOLDER_DOCS_ID en .env")
    if not PROJECT_ID:
        raise ValueError("Falta PROJECT_ID en .env")

    prompt_base = leer_prompt()
    hoja = obtener_hoja()
    valores = hoja.get_all_values()

    # Índices base-0 para leer el array; base-1 para update_cell
    col_fuente_arr  = letra_a_columna(COL_FUENTE_LETTER) - 1
    col_destino_idx = letra_a_columna(COL_DESTINO_LETTER)       # base-1
    col_destino_arr = col_destino_idx - 1                        # base-0

    procesadas = 0
    for idx in range(1, len(valores)):
        fila = valores[idx]
        num_fila_real = idx + 1

        url_transcripcion = fila[col_fuente_arr].strip()  if len(fila) > col_fuente_arr  else ""
        ya_analizado      = fila[col_destino_arr].strip() if len(fila) > col_destino_arr else ""

        if url_transcripcion and not ya_analizado:
            procesar_fila_analisis(hoja, num_fila_real, url_transcripcion,
                                   col_destino_idx, prompt_base)
            procesadas += 1
            time.sleep(2)  # pausa entre filas para respetar cuotas de la API

    if procesadas == 0:
        logger.info("No se encontraron filas pendientes de análisis.")
    else:
        logger.info(f"Análisis batch completado. Filas procesadas: {procesadas}.")


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("MOTOR DE ANÁLISIS DE LLAMADAS DE VENTAS")
    print("=" * 50 + "\n")
    ejecutar_analisis()
