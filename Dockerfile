# Usamos Python 3.11 que es la que tienes en tu entorno .venv
FROM python:3.11-slim

# Instalamos FFMPEG (Crítico para el preprocesamiento de audio)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Establecemos el directorio de trabajo
WORKDIR /app

# Copiamos las dependencias primero para aprovechar el caché de capas de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos todo nuestro código limpio
COPY . .

# Cloud Run inyecta el puerto 8080 por defecto
ENV PORT="8080"
EXPOSE $PORT

# Lanzamos el servidor con uvicorn
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]