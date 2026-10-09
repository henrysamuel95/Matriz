# Importación de los módulos necesarios de Odoo, pymongo y excepciones personalizadas
from odoo import fields, models, api  # Importa las clases y métodos básicos de Odoo
from pymongo import MongoClient  # Cliente para conectarse a MongoDB
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure  # Excepciones específicas de pymongo
from odoo.exceptions import UserError  # Excepción de Odoo para manejar errores personalizados



class MongoDBModel(models.Model):
    _name = 'mongo.db.model'  # Nombre técnico del modelo
    _description = 'Modelo que interactúa con MongoDB'  # Descripción del modelo

    _inherit = ['mail.thread']  # Heredamos de mail.thread para añadir soporte de mensajes y seguimiento

    # Definición de campos en el modelo
    name = fields.Char(string="Nombre")  # Campo para el nombre del registro
    description = fields.Char(string="Descripción")  # Campo para una descripción
    search_term = fields.Char(string="Término de Búsqueda")  # Campo para guardar el término de búsqueda
    search_results = fields.Text(string="Resultados de Búsqueda")  # Campo para mostrar los resultados de las consultas

    # Configuración de conexión a la base de datos MongoDB
    MONGO_HOST = "localhost"  # Dirección del host de MongoDB
    MONGO_PORT = 27017  # Puerto donde MongoDB escucha
    MONGO_URI = "mongodb://localhost:27017/escuela"  # URI de conexión completa a MongoDB
    MONGO_DATABASE = "compas_db"  # Nombre de la base de datos a usar
    MONGO_COLLECTION = "contrataciones"  # Nombre de la colección dentro de la base de datos

    @api.model
    def connect_mongo(self):
        """
        Establece una conexión a la base de datos de MongoDB.
        Si ocurre un error, lanza una excepción de UserError.
        """
        try:
            # Establece un cliente de MongoDB con un tiempo de espera para selección de servidor
            client = MongoClient(self.MONGO_URI, serverSelectionTimeoutMS=1000)
            # Obtiene la base de datos especificada
            db = client[self.MONGO_DATABASE]
            return db  # Retorna la conexión a la base de datos
        except ServerSelectionTimeoutError as error:
            # Manejo de errores de tiempo de espera en la conexión
            raise UserError(f"Tiempo excedido al conectar a MongoDB: {error}")
        except ConnectionFailure as error:
            # Manejo de errores de fallo de conexión
            raise UserError(f"Fallo al conectarse a MongoDB: {error}")
        except Exception as error:
            # Manejo de cualquier otro error inesperado
            raise UserError(f"Error desconocido al conectar a MongoDB: {error}")

    def mostrar_datos(self, filtro=""):
        """
        Muestra los resultados de búsqueda en el campo 'search_results'.
        Parámetro:
        - filtro: Cadena para filtrar resultados en base al campo 'nombre'.
        """
        db = self.connect_mongo()  # Conecta a la base de datos
        collection = db[self.MONGO_COLLECTION]  # Obtiene la colección especificada
        query = {}  # Define una consulta vacía (sin filtros por defecto)
        if filtro:  # Si se proporciona un filtro
            # Define una consulta basada en expresiones regulares (case insensitive)
            query = {"nombre": {"$regex": filtro, "$options": "i"}}

        registros = collection.find(query)  # Busca los registros que coinciden con la consulta
        self.search_results = ""  # Limpia los resultados de búsqueda previos
        for registro in registros:
            # Construye una cadena con la información de cada registro encontrado
            self.search_results += f"ID: {registro.get('_id', '')}, Nombre: {registro.get('nombre', '')}, Descripción: {registro.get('descripcion', '')}\n"

    def button_connect(self):
        """
        Maneja el evento de conexión al hacer clic en un botón 'Conectar'.
        """
        try:
            self.connect_mongo()  # Intenta establecer la conexión a MongoDB
            # Usamos print en lugar de message_post para mostrar un mensaje de éxito
            print("Conexión a MongoDB realizada con éxito.")
        except UserError as e:
            # Si ocurre un error, imprimimos el mensaje en consola
            print(f"Error al conectar a MongoDB: {str(e)}")
