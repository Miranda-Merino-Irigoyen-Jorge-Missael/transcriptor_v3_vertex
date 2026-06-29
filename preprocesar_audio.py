import os
import subprocess
import logging

logger = logging.getLogger(__name__)

def obtener_duracion(ruta_archivo: str) -> float:
    comando = [
        "ffprobe", 
        "-v", "error", 
        "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", 
        ruta_archivo
    ]
    try:
        resultado = subprocess.run(
            comando, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True, 
            check=True
        )
        return float(resultado.stdout.strip())
    except subprocess.CalledProcessError as e:
        logger.error(f"Falla al obtener duración con ffprobe para {ruta_archivo}: {e.stderr}")
        return 0.0

def extraer_y_validar_audio(ruta_entrada: str, carpeta_trabajo: str) -> str:
    ruta_audio_maestro = os.path.join(carpeta_trabajo, "audio_maestro_extraido.flac")
    logger.info(f"Iniciando extracción de audio para: {ruta_entrada}")
    duracion_original = obtener_duracion(ruta_entrada)
    
    comando_extraccion = [
        "ffmpeg",
        "-y",
        "-i", ruta_entrada,
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "flac",
        ruta_audio_maestro
    ]
    
    try:
        subprocess.run(comando_extraccion, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Falla en la extracción con FFMPEG: {e.stderr}")
        raise RuntimeError("Error al extraer el audio del archivo original.")
        
    duracion_audio = obtener_duracion(ruta_audio_maestro)
    diferencia = abs(duracion_original - duracion_audio)
    
    if duracion_original > 0 and diferencia <= 2.0:
        logger.info(f"✅ EXTRACCIÓN CONFIRMADA: La duración del video original ({duracion_original:.2f}s) coincide con el audio extraído ({duracion_audio:.2f}s). Diferencia: {diferencia:.2f}s.")
    elif duracion_original > 0:
        logger.warning(f"⚠️ ADVERTENCIA: Diferencia de duración de {diferencia:.2f}s. Original: {duracion_original:.2f}s, Extraído: {duracion_audio:.2f}s.")
    else:
        logger.warning("No se pudo confirmar la duración exacta, pero la extracción finalizó.")
        
    return ruta_audio_maestro

def preprocesar_con_ffmpeg(ruta_entrada: str, carpeta_salida: str, duracion_segmento_segundos: int = 3600) -> list:
    if not os.path.exists(carpeta_salida):
        os.makedirs(carpeta_salida, exist_ok=True)
        
    carpeta_trabajo = os.path.dirname(carpeta_salida) 
    ruta_audio_maestro = extraer_y_validar_audio(ruta_entrada, carpeta_trabajo)
    
    patron_salida = os.path.join(carpeta_salida, "segmento_%03d.flac")
    comando_segmentacion = [
        "ffmpeg",
        "-y",
        "-i", ruta_audio_maestro,
        "-c:a", "flac",  # <-- CAMBIO CRÍTICO: Re-codificamos el fragmento para asegurar cabeceras legibles para Vertex.
        "-f", "segment",
        "-segment_time", str(duracion_segmento_segundos),
        "-reset_timestamps", "1",
        patron_salida
    ]
    
    logger.info(f"Iniciando segmentación del audio maestro: {ruta_audio_maestro}")
    
    try:
        subprocess.run(comando_segmentacion, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        logger.info("Segmentación completada exitosamente.")
        
        archivos_generados = sorted([f for f in os.listdir(carpeta_salida) if f.endswith(".flac")])
        rutas_segmentos = [os.path.join(carpeta_salida, f) for f in archivos_generados]
        
        if not rutas_segmentos:
            raise RuntimeError("FFMPEG no generó archivos de salida al segmentar.")
            
        return rutas_segmentos
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Falla al segmentar con FFMPEG: {e.stderr}")
        raise RuntimeError("Error al segmentar el audio maestro.")