import os
import subprocess
import logging

# Configuración de logging
logger = logging.getLogger(__name__)

def preprocesar_con_ffmpeg(ruta_entrada: str, carpeta_salida: str, duracion_segmento_segundos: int = 3600) -> list:
    """
    Estandariza, normaliza y segmenta un archivo de audio utilizando FFMPEG.
    Optimizado para Cloud Run y Vertex AI.
    """
    if not os.path.exists(carpeta_salida):
        os.makedirs(carpeta_salida, exist_ok=True)
        
    patron_salida = os.path.join(carpeta_salida, "segmento_%03d.flac")
    
    comando = [
        "ffmpeg", 
        "-y",                 
        "-i", ruta_entrada,   
        "-af", "loudnorm=I=-20:LRA=11:TP=-1.5", 
        "-ar", "16000",       
        "-ac", "1",           
        "-c:a", "flac",       
        "-f", "segment",      
        "-segment_time", str(duracion_segmento_segundos), 
        "-reset_timestamps", "1",
        patron_salida
    ]
    
    logger.info(f"Iniciando normalización y segmentación para: {ruta_entrada}")
    
    try:
        subprocess.run(
            comando, 
            check=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        logger.info("Procesamiento FFMPEG completado.")
        
        archivos_generados = sorted([f for f in os.listdir(carpeta_salida) if f.endswith(".flac")])
        rutas_segmentos = [os.path.join(carpeta_salida, f) for f in archivos_generados]
        
        if not rutas_segmentos:
            raise RuntimeError("FFMPEG no generó archivos de salida.")
            
        return rutas_segmentos
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Falla en FFMPEG: {e.stderr}")
        raise RuntimeError("Error al decodificar el audio con FFMPEG.")