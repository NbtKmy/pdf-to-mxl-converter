from flask import *
from werkzeug.utils import secure_filename
import glob
import docker
import os
import shutil

from formTemplate import pdfUpload

UPLOAD_FOLDER = '/code/src/mediafiles'
OUTPUT_FOLDER = '/code/src/output'

server = Flask(__name__)
server.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
server.config['SECRET_KEY'] = 'your_secret_key'


@server.route('/', methods=['GET', 'POST'])
def main():
    form = pdfUpload()

    if form.validate_on_submit():

        # delete all files in UPLOAD_FOLDER 
        for f in glob.glob(UPLOAD_FOLDER + '/*'):
            if os.path.isfile(f):
                os.remove(f)
        # delete alle dirs & files under OUTPUT_FOLDER
        for d in glob.glob(OUTPUT_FOLDER + '/*'):
            if os.path.isdir(d):
                shutil.rmtree(d)
        
        x = form.pdf.data
        filename = secure_filename(x.filename)
        x.save(os.path.join(server.config['UPLOAD_FOLDER'], filename))
        flash(filename + ' is uploaded!')
        
        client = docker.from_env()
        audi_cont = client.containers.get('audiveris')
        cmd = '/bin/sh -c "/Audiveris/bin/Audiveris -batch -export -output /output /input/*"'
        proc = audi_cont.exec_run(cmd)

        filename_naked = os.path.splitext(filename)[0]
        download_path = OUTPUT_FOLDER + '/' + filename_naked + '/*.mxl'
        mxl_files = glob.glob(download_path)
        mxl_file = mxl_files[0]
        flash(mxl_files)
        
        
        if proc[0] == 0:
            return send_file(mxl_file, as_attachment = True, mimetype = 'application/vnd.recordare.musicxml')
        else:
            flash('An error occured...')
            flash(proc[0])
            return redirect(url_for('main', form=form))
        

    return render_template('index.html', form=form)


if __name__ == '__main__':
    server.run(debug=True, host='0.0.0.0', port=8888, threaded=True)