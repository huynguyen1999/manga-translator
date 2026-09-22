# Adopted from https://github.com/yt-dlp/yt-dlp/tree/master/devscripts

def read_file(fname):
    with open(fname, encoding='utf-8') as f:
        return f.read()


def write_file(fname, content, mode='w'):
    with open(fname, mode, encoding='utf-8') as f:
        return f.write(content)
