import os
import re
import io
import json
import logging
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.cloud import storage

logger = logging.getLogger(__name__)

# Alcance necesario para leer los archivos de Drive
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
DOMINIO_FIRMA = "supportmendoza.com"

def obtener_servicio_drive():
    """
    Autentica con la cuenta de Workspace usando el token almacenado en las variables de entorno.
    En producción (Cloud Run), esta variable se alimentará desde Secret Manager.
    """
    token_string = os.getenv("WORKSPACE_TOKEN_JSON")
    if not token_string:
        raise ValueError("Falta la variable de entorno WORKSPACE_TOKEN_JSON con las credenciales de Drive.")
    
    try:
        token_dict = json.loads(token_string)
        creds = Credentials.from_authorized_user_info(token_dict, SCOPES)
        servicio_drive = build('drive', 'v3', credentials=creds)
        return servicio_drive
    except Exception as e:
        logger.error(f"Error al cargar credenciales de Workspace: {e}")
        raise

def extraer_id_drive(url: str) -> str:
    """Extrae el ID del archivo de un enlace estándar de Google Drive."""
    match_file = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if match_file:
        return match_file.group(1)
    
    match_id = re.search(r'id=([a-zA-Z0-9-_]+)', url)
    if match_id:
        return match_id.group(1)
        
    raise ValueError(f"No se pudo extraer un ID válido del enlace: {url}")

def validar_y_descargar_drive(url: str, carpeta_destino: str) -> str:
    """
    Valida que el archivo pertenezca a la firma legal y lo descarga localmente.
    """
    servicio = obtener_servicio_drive()
    file_id = extraer_id_drive(url)
    
    # 1. Validar el dominio del propietario
    meta = servicio.files().get(
        fileId=file_id, 
        fields="name, owners, driveId",
        supportsAllDrives=True
    ).execute()
    
    owners = meta.get('owners', [])
    if owners:
        correo_propietario = owners[0].get('emailAddress', '')
        if not correo_propietario.endswith(f"@{DOMINIO_FIRMA}"):
            raise PermissionError(f"ACCESO DENEGADO: El archivo pertenece a {correo_propietario}, no a {DOMINIO_FIRMA}.")
    elif not meta.get('driveId'):
        # Si no tiene owner y no es un Shared Drive, bloqueamos por seguridad
        raise PermissionError("ACCESO DENEGADO: No se pudo verificar la propiedad del archivo.")

    # 2. Descargar el archivo
    nombre_archivo = meta['name']
    os.makedirs(carpeta_destino, exist_ok=True)
    ruta_completa = os.path.join(carpeta_destino, nombre_archivo)
    
    logger.info(f"Descargando audio validado de Drive: {nombre_archivo}")
    request = servicio.files().get_media(fileId=file_id, supportsAllDrives=True)
    
    with io.FileIO(ruta_completa, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            
    return ruta_completa

def subir_resultado_gcs(ruta_local: str, bucket_name: str, cliente_id: str) -> str:
    """
    Sube el archivo JSON final al bucket de Google Cloud Storage.
    """
    # Al estar en Cloud Run, storage.Client() toma automáticamente las credenciales de la Service Account
    cliente_storage = storage.Client()
    bucket = cliente_storage.bucket(bucket_name)
    
    nombre_archivo = os.path.basename(ruta_local)
    # Organizamos los resultados en carpetas por cliente
    ruta_blob = f"transcripciones/{cliente_id}/{nombre_archivo}"
    
    blob = bucket.blob(ruta_blob)
    blob.upload_from_filename(ruta_local)
    
    uri_gcs = f"gs://{bucket_name}/{ruta_blob}"
    logger.info(f"Resultado subido exitosamente a GCS: {uri_gcs}")
    
    return uri_gcs