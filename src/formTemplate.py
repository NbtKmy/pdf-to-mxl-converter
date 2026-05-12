from flask_wtf import FlaskForm
from wtforms import MultipleFileField, RadioField
from wtforms.validators import InputRequired


class imageUpload(FlaskForm):
    img = MultipleFileField(
        'score files',
        validators=[InputRequired()],
    )
    output_format = RadioField(
        'Output format',
        choices=[
            ('edit', 'Open in editor'),
            ('mei', 'Download MEI'),
            ('mxl', 'Download MusicXML'),
        ],
        default='edit',
    )
