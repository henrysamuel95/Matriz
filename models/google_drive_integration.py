from odoo import models, fields, api
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

class GoogleDriveIntegration(models.Model):
    _name = 'matriz.google_drive'
    _description = 'Google Drive Integration'

    name = fields.Char(string="Name")
    file_id = fields.Char(string="Google Drive File ID")
    file_url = fields.Char(string="Google Drive File URL", compute='_compute_file_url')
    file_binary = fields.Binary(string="File")
    folder_id = fields.Char(string="Google Drive Folder ID")  # Agregar este campo

    def _compute_file_url(self):
        for record in self:
            if record.file_id:
                record.file_url = f"https://drive.google.com/drive/folders/d/{record.file_id}/view"

    def _get_google_drive_service(self):
        SCOPES = ['https://www.googleapis.com/auth/drive.folders']
        credentials = service_account.Credentials.from_service_account_file('C:/odoo_dev/Matriz/service_account_credentials.json', scopes=SCOPES)
        return build('drive', 'v3', credentials=credentials)

    def upload_file_to_google_drive(self):
        for record in self:
            if record.file_binary and record.folder_id:
                service = self._get_google_drive_service()
                if service:
                    media = MediaFileUpload(record.file_binary.decode('utf-8'), mimetype='application/octet-stream')
                    file_metadata = {'name': record.name, 'parents': [record.folder_id]}
                    file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                    record.file_id = file.get('id')
                    record._compute_file_url()
        return True
