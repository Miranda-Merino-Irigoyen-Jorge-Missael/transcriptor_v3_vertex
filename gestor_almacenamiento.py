import os
import re
import io
import logging
import requests
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import dropbox
from google.cloud import storage  # NUEVO: Importación para usar Buckets de GCP

logger = logging.getLogger(__name__)

# Permisos requeridos para Drive y Google Docs
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/spreadsheets'
]

# --- 1. AUTENTICACIÓN OAUTH ---
def obtener_credenciales_oauth():
    """
    Maneja el flujo OAuth. Genera un 'token.json' a partir de un 'credentials.json'.
    Si el token expira, lo refresca automáticamente.
    """
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Asegúrate de tener tu archivo credentials.json (OAuth) en la raíz
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            
    return creds

def obtener_servicio_drive():
    return build('drive', 'v3', credentials=obtener_credenciales_oauth())

def obtener_servicio_docs():
    return build('docs', 'v1', credentials=obtener_credenciales_oauth())

# --- 2. DESCARGA DESDE DROPBOX ---
class DropboxDownloader:
    def __init__(self):
        token = os.getenv("DROPBOX_TOKEN")
        if not token: 
            raise ValueError("DROPBOX_TOKEN no encontrado en el archivo .env")
        
        self.dbx = dropbox.Dropbox(token)
        try:
            account = self.dbx.users_get_current_account()
            if hasattr(account, 'team') and account.team:
                root_ns = account.root_info.root_namespace_id
                self.dbx = self.dbx.with_path_root(dropbox.common.PathRoot.namespace_id(root_ns))
        except Exception as e:
            logger.warning(f"No se pudo configurar path_root de Dropbox: {e}")

    def _get_file_name_from_url(self, url: str) -> str:
        """Extrae el nombre del archivo directamente desde la URL como último recurso."""
        path_part = url.split('?')[0].rstrip('/')
        return path_part.split('/')[-1] or "media_dropbox.mp4"

    def _download_direct_http(self, shared_link: str, local_path: Path) -> None:
        """Fallback: descarga directa vía HTTP convirtiendo el enlace compartido a enlace de descarga."""
        if 'dl=0' in shared_link:
            direct_url = shared_link.replace('dl=0', 'dl=1')
        elif 'dl=1' not in shared_link:
            sep = '&' if '?' in shared_link else '?'
            direct_url = f"{shared_link}{sep}dl=1"
        else:
            direct_url = shared_link

        logger.info(f"Usando descarga HTTP directa como fallback para: {local_path.name}")
        with requests.get(direct_url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get('Content-Type', '')
            if 'text/html' in content_type:
                raise ValueError(
                    f"Dropbox devolvió HTML en lugar del archivo multimedia (Content-Type: {content_type}). "
                    "El enlace puede haber expirado o requerir autenticación."
                )
            with open(local_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

    def download(self, shared_link: str, download_dir: str) -> str:
        path_dir = Path(download_dir)
        path_dir.mkdir(parents=True, exist_ok=True)

        file_name = None
        try:
            metadata = self.dbx.sharing_get_shared_link_metadata(url=shared_link)
            if hasattr(metadata, 'name') and metadata.name:
                raw = metadata.name
                file_name = "".join(c for c in raw if c.isalnum() or c in ('.', '_', '-')).rstrip()
                if "." in raw:
                    ext = raw.split(".")[-1]
                    if not file_name.endswith(f".{ext}"):
                        file_name = f"{file_name}.{ext}"
        except Exception as meta_err:
            logger.warning(f"No se pudo obtener metadatos de Dropbox ({meta_err}). Se usará nombre desde URL.")

        if not file_name:
            file_name = self._get_file_name_from_url(shared_link)

        local_path = path_dir / file_name
        logger.info(f"Descargando archivo directo de Dropbox: {file_name}")

        try:
            self.dbx.sharing_get_shared_link_file_to_file(
                download_path=str(local_path),
                url=shared_link
            )
        except Exception as sdk_err:
            logger.warning(f"Descarga SDK falló ({sdk_err}). Intentando fallback HTTP directo...")
            try:
                self._download_direct_http(shared_link, local_path)
            except Exception as http_err:
                logger.error(f"Fallback HTTP también falló: {http_err}")
                raise ValueError("No se pudo extraer ningún archivo del enlace de Dropbox.")

        return str(local_path)

# --- 3. GESTIÓN CENTRALIZADA DE DESCARGAS ---
def extraer_id_drive(url: str) -> str:
    match_file = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if match_file: return match_file.group(1)
    
    match_id = re.search(r'id=([a-zA-Z0-9-_]+)', url)
    if match_id: return match_id.group(1)
        
    raise ValueError(f"No se pudo extraer un ID válido del enlace de Drive: {url}")

def descargar_audio(url: str, carpeta_destino: str) -> str:
    """Descarga de Drive o Dropbox con validación estricta de peso para evitar cortes."""
    if "dropbox.com" in url.lower():
        logger.info("Enlace de Dropbox detectado.")
        downloader = DropboxDownloader()
        return downloader.download(url, carpeta_destino)
        
    elif "drive.google.com" in url.lower():
        logger.info("Enlace de Google Drive detectado.")
        servicio = obtener_servicio_drive()
        file_id = extraer_id_drive(url)
        
        # Consultamos cuánto DEBE pesar el archivo
        meta = servicio.files().get(fileId=file_id, fields="name, size", supportsAllDrives=True).execute()
        nombre_archivo = meta['name']
        tamano_esperado = int(meta.get('size', 0))
        
        nombre_archivo_seguro = re.sub(r'[\\/*?:"<>|]', "_", nombre_archivo)
        os.makedirs(carpeta_destino, exist_ok=True)
        ruta_completa = os.path.join(carpeta_destino, nombre_archivo_seguro)
        
        logger.info(f"Iniciando descarga. Peso total en Drive: {tamano_esperado / (1024*1024):.2f} MB")
        
        request = servicio.files().get_media(fileId=file_id, supportsAllDrives=True)
        with io.FileIO(ruta_completa, 'wb') as fh:
            # Descargamos en bloques grandes (100MB) para que la conexión no expire
            downloader = MediaIoBaseDownload(fh, request, chunksize=100*1024*1024)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    logger.info(f"Descargando: {int(status.progress() * 100)}% completado...")
                    
        # Validación crítica: Verificamos si bajó completo
        tamano_descargado = os.path.getsize(ruta_completa)
        if tamano_esperado > 0 and tamano_descargado < tamano_esperado:
            raise RuntimeError(f"DESCARGA TRUNCADA: El archivo en la nube pesa {tamano_esperado} bytes, pero solo se descargaron {tamano_descargado} bytes.")
            
        return ruta_completa
    
    else:
        raise ValueError(f"URL no soportada: {url}")

# --- NUEVAS FUNCIONES PARA GOOGLE CLOUD STORAGE (GCP BUCKETS) ---
def subir_a_gcs(ruta_local: str, nombre_bucket: str, ruta_destino_gcs: str) -> str:
    """
    Sube un archivo local a un bucket de Google Cloud Storage y retorna la URI gs://
    Ideal para pasar archivos pesados a Vertex AI de forma nativa.
    """
    logger.info(f"Subiendo archivo a GCP Bucket: gs://{nombre_bucket}/{ruta_destino_gcs}...")
    cliente = storage.Client()
    bucket = cliente.bucket(nombre_bucket)
    blob = bucket.blob(ruta_destino_gcs)
    
    blob.upload_from_filename(ruta_local)
    
    uri_gcs = f"gs://{nombre_bucket}/{ruta_destino_gcs}"
    logger.info(f"✅ Archivo subido exitosamente a GCS: {uri_gcs}")
    return uri_gcs

def eliminar_de_gcs(nombre_bucket: str, ruta_destino_gcs: str):
    """
    Elimina un archivo del bucket de GCP para no acumular basura de archivos temporales.
    """
    try:
        cliente = storage.Client()
        bucket = cliente.bucket(nombre_bucket)
        blob = bucket.blob(ruta_destino_gcs)
        blob.delete()
        logger.info(f"🧹 Archivo limpiado de GCS: gs://{nombre_bucket}/{ruta_destino_gcs}")
    except Exception as e:
        logger.warning(f"No se pudo eliminar el archivo temporal de GCS: {e}")

# --- 4. CREACIÓN DE GOOGLE DOCS ---
def crear_google_doc_con_transcripcion(titulo: str, transcripcion_formateada: str, carpeta_destino_id: str) -> str:
    """
    Crea un documento en Google Drive y le inserta todo el texto de la transcripción y el análisis.
    Devuelve el enlace al documento generado.
    """
    drive_service = obtener_servicio_drive()
    docs_service = obtener_servicio_docs()

    logger.info(f"Creando Google Doc: {titulo}")
    
    doc_metadata = {
        'name': titulo,
        'mimeType': 'application/vnd.google-apps.document',
        'parents': [carpeta_destino_id]
    }
    doc = drive_service.files().create(body=doc_metadata, fields='id').execute()
    document_id = doc.get('id')

    requests = [
        {
            'insertText': {
                'location': {
                    'index': 1,
                },
                'text': transcripcion_formateada
            }
        }
    ]
    
    try:
        docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()
        url_doc = f"https://docs.google.com/document/d/{document_id}/edit"
        logger.info(f"Google Doc creado con éxito: {url_doc}")
        return url_doc
    except Exception as e:
        logger.error(f"Error inyectando texto al documento {document_id}: {e}")
        raise


# --- 5. LECTURA DE GOOGLE DOCS ---
def leer_contenido_google_doc(document_id: str) -> str:
    """Lee y devuelve el texto plano completo de un Google Doc."""
    docs_service = obtener_servicio_docs()
    doc = docs_service.documents().get(documentId=document_id).execute()
    partes = []
    for elemento in doc.get('body', {}).get('content', []):
        if 'paragraph' in elemento:
            for pe in elemento['paragraph'].get('elements', []):
                partes.append(pe.get('textRun', {}).get('content', ''))
    return ''.join(partes)


# --- 6. CREACIÓN DE GOOGLE DOCS CON FORMATO MARKDOWN ---
def _inline_markdown(texto: str, pos_base: int):
    """
    Procesa formato inline Markdown (***bold+italic***, **bold**, *italic*).
    Devuelve (texto_limpio, [(inicio_abs, fin_abs, tipo)]).
    """
    patron = re.compile(r'\*{3}(.+?)\*{3}|\*{2}(.+?)\*{2}|\*(.+?)\*')
    partes = []
    spans = []
    pos_limpia = pos_base
    ultimo_fin = 0

    for m in patron.finditer(texto):
        antes = texto[ultimo_fin:m.start()]
        partes.append(antes)
        pos_limpia += len(antes)

        if m.group(1) is not None:
            contenido, tipo = m.group(1), 'bold_italic'
        elif m.group(2) is not None:
            contenido, tipo = m.group(2), 'bold'
        else:
            contenido, tipo = m.group(3), 'italic'

        inicio_span = pos_limpia
        partes.append(contenido)
        pos_limpia += len(contenido)
        spans.append((inicio_span, pos_limpia, tipo))
        ultimo_fin = m.end()

    partes.append(texto[ultimo_fin:])
    return ''.join(partes), spans


def _markdown_a_requests(texto_markdown: str) -> list:
    """
    Parsea Markdown y genera una lista de requests para Google Docs batchUpdate.
    Soporta: H1/H2/H3, **negrita**, *cursiva*, ***ambas***, listas con '- '.
    """
    import re as _re

    lineas = texto_markdown.splitlines()
    batch = []
    parrafos_estilo = []   # (inicio, fin, estilo_str, es_bullet)
    spans_inline = []
    partes_texto = []
    pos = 1  # índice 1-based en el documento

    for linea in lineas:
        estilo = "NORMAL_TEXT"
        es_bullet = False
        texto_linea = linea

        if linea.startswith('### '):
            estilo, texto_linea = "HEADING_3", linea[4:]
        elif linea.startswith('## '):
            estilo, texto_linea = "HEADING_2", linea[3:]
        elif linea.startswith('# '):
            estilo, texto_linea = "HEADING_1", linea[2:]
        elif _re.match(r'^[-*] ', linea):
            es_bullet, texto_linea = True, linea[2:]
        elif _re.match(r'^-{3,}$|^={3,}$', linea):
            texto_linea = ""  # separador → línea vacía

        texto_procesado, spans = _inline_markdown(texto_linea, pos)
        spans_inline.extend(spans)

        inicio_parrafo = pos
        longitud = len(texto_procesado)
        fin_parrafo = pos + longitud + 1  # +1 por el '\n'

        if texto_procesado or estilo != "NORMAL_TEXT" or es_bullet:
            parrafos_estilo.append((inicio_parrafo, fin_parrafo, estilo, es_bullet))

        partes_texto.append(texto_procesado + '\n')
        pos = fin_parrafo

    texto_completo = ''.join(partes_texto)
    if not texto_completo.strip():
        return []

    # Request 1: Insertar todo el texto de una sola vez
    batch.append({
        'insertText': {
            'location': {'index': 1},
            'text': texto_completo
        }
    })

    # Estilos de párrafo (encabezados y bullets)
    for inicio, fin, estilo, es_bullet in parrafos_estilo:
        if es_bullet:
            batch.append({
                'createParagraphBullets': {
                    'range': {'startIndex': inicio, 'endIndex': fin},
                    'bulletPreset': 'BULLET_DISC_CIRCLE_SQUARE'
                }
            })
        elif estilo != "NORMAL_TEXT":
            batch.append({
                'updateParagraphStyle': {
                    'range': {'startIndex': inicio, 'endIndex': fin},
                    'paragraphStyle': {'namedStyleType': estilo},
                    'fields': 'namedStyleType'
                }
            })

    # Estilos de texto inline (bold / italic)
    for inicio, fin, tipo in spans_inline:
        if tipo == 'bold_italic':
            style_obj, fields = {'bold': True, 'italic': True}, 'bold,italic'
        elif tipo == 'bold':
            style_obj, fields = {'bold': True}, 'bold'
        else:
            style_obj, fields = {'italic': True}, 'italic'

        batch.append({
            'updateTextStyle': {
                'range': {'startIndex': inicio, 'endIndex': fin},
                'textStyle': style_obj,
                'fields': fields
            }
        })

    return batch


def crear_google_doc_desde_markdown(titulo: str, texto_markdown: str, carpeta_destino_id: str) -> str:
    """
    Crea un Google Doc con formato visual (encabezados, negrita, bullets)
    a partir de texto en Markdown. Devuelve la URL del documento creado.
    """
    drive_service = obtener_servicio_drive()
    docs_service = obtener_servicio_docs()

    logger.info(f"Creando Google Doc con formato Markdown: {titulo}")
    doc_metadata = {
        'name': titulo,
        'mimeType': 'application/vnd.google-apps.document',
        'parents': [carpeta_destino_id]
    }
    doc = drive_service.files().create(body=doc_metadata, fields='id').execute()
    document_id = doc.get('id')

    batch_requests = _markdown_a_requests(texto_markdown)
    if batch_requests:
        docs_service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': batch_requests}
        ).execute()

    url_doc = f"https://docs.google.com/document/d/{document_id}/edit"
    logger.info(f"Google Doc con formato creado: {url_doc}")
    return url_doc