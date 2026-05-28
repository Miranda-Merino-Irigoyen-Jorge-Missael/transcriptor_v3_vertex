import os
import re
import io
import logging
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import dropbox

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

    def download(self, shared_link: str, download_dir: str) -> str:
        path_dir = Path(download_dir)
        path_dir.mkdir(parents=True, exist_ok=True)
        try:
            metadata = self.dbx.sharing_get_shared_link_metadata(url=shared_link)
            
            # CORRECCIÓN: Usamos hasattr(metadata, 'name') en lugar de exigir path_lower
            if hasattr(metadata, 'name'):
                file_name = "".join(c for c in metadata.name if c.isalnum() or c in ('.', '_', '-')).rstrip()
                
                if "." in metadata.name:
                    ext = metadata.name.split(".")[-1]
                    if not file_name.endswith(ext):
                        file_name = f"{file_name}.{ext}"
                    
                local_path = path_dir / file_name
                logger.info(f"Descargando archivo directo de Dropbox: {file_name}")
                self.dbx.sharing_get_shared_link_file_to_file(
                    download_path=str(local_path),
                    url=shared_link
                )
                return str(local_path)
            else:
                raise ValueError("El enlace de Dropbox no contiene un nombre de archivo válido.")
                
        except Exception as e:
            logger.error(f"Error descargando de Dropbox: {e}")
            raise ValueError("No se pudo extraer ningún archivo del enlace de Dropbox.")

# --- 3. GESTIÓN CENTRALIZADA DE AUDIOS ---
def extraer_id_drive(url: str) -> str:
    match_file = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if match_file: return match_file.group(1)
    
    match_id = re.search(r'id=([a-zA-Z0-9-_]+)', url)
    if match_id: return match_id.group(1)
        
    raise ValueError(f"No se pudo extraer un ID válido del enlace de Drive: {url}")

def descargar_audio(url: str, carpeta_destino: str) -> str:
    """Detecta el proveedor (Drive/Dropbox) y ejecuta la descarga correspondiente."""
    
    if "dropbox.com" in url.lower():
        logger.info("Enlace de Dropbox detectado.")
        downloader = DropboxDownloader()
        return downloader.download(url, carpeta_destino)
        
    elif "drive.google.com" in url.lower():
        logger.info("Enlace de Google Drive detectado.")
        servicio = obtener_servicio_drive()
        file_id = extraer_id_drive(url)
        
        meta = servicio.files().get(fileId=file_id, fields="name", supportsAllDrives=True).execute()
        nombre_archivo = meta['name']
        os.makedirs(carpeta_destino, exist_ok=True)
        ruta_completa = os.path.join(carpeta_destino, nombre_archivo)
        
        request = servicio.files().get_media(fileId=file_id, supportsAllDrives=True)
        with io.FileIO(ruta_completa, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                
        return ruta_completa
    
    else:
        raise ValueError(f"URL no soportada. Debe ser Drive o Dropbox. URL recibida: {url}")

# --- 4. CREACIÓN DE GOOGLE DOCS ---
def crear_google_doc_con_transcripcion(titulo: str, transcripcion_formateada: str, carpeta_destino_id: str) -> str:
    """
    Crea un documento en Google Drive y le inserta todo el texto de la transcripción y el análisis.
    Devuelve el enlace al documento generado.
    """
    drive_service = obtener_servicio_drive()
    docs_service = obtener_servicio_docs()

    logger.info(f"Creando Google Doc: {titulo}")
    
    # 1. Crear documento vacío dentro del folder
    doc_metadata = {
        'name': titulo,
        'mimeType': 'application/vnd.google-apps.document',
        'parents': [carpeta_destino_id]
    }
    doc = drive_service.files().create(body=doc_metadata, fields='id').execute()
    document_id = doc.get('id')

    # 2. Inyectar el texto masivo
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