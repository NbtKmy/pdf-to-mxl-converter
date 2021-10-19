from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed


class pdfUpload(FlaskForm):
    pdf = FileField([FileRequired(), FileAllowed(['pdf', 'jpg', 'png', 'tif'], 'Image file only!')])
