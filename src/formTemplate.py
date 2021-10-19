from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed


class imageUpload(FlaskForm):
    img = FileField([FileRequired(), FileAllowed(['pdf', 'jpg', 'png', 'tif'], 'Image file only!')])
