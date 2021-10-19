from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed


class imageUpload(FlaskForm):
    img = FileField('image', validators=[FileRequired(), FileAllowed(['pdf'], 'PDF file only!')])
