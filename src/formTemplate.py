from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import RadioField


class imageUpload(FlaskForm):
    img = FileField(
        'image',
        validators=[FileRequired(), FileAllowed(['pdf'], 'PDF file only!')],
    )
    output_format = RadioField(
        'Output format',
        choices=[
            ('mei', 'MEI (with facsimile)'),
            ('mxl', 'MusicXML (.mxl)'),
        ],
        default='mei',
    )
