#!/usr/bin/env python
from yahoogroupsapi import YahooGroupsAPI
import json
import email
import urllib
import os
from os.path import basename
from xml.sax.saxutils import unescape
import argparse
import getpass
import sys

def unescape_html(string):
    return unescape(string, {"&quot;": '"', "&apos;": "'", "&#39;": "'"})

def get_best_photoinfo(photoInfoArr):
    rs = {'tn': 0, 'sn': 1, 'hr': 2, 'or': 3}
    best = photoInfoArr[0]
    for info in photoInfoArr:
        if rs[info['photoType']] >= rs[best['photoType']]:
            best = info
    return best


def archive_email(yga, reattach=True, save=True, resume=False, skip=[]):
    msg_json = yga.messages()
    count = msg_json['totalRecords']

    msg_json = yga.messages(count=count)
    print "Group has %s messages, got %s" % (count, msg_json['numRecords'])

    for message in msg_json['messages']:
        id = message['messageId']
        eml_fname = "%s.eml" % (id,)

        if resume and os.path.isfile(eml_fname):
            print "* Skipping %s as it already exists" % (id)
            continue

        if id in skip:
            print "* Skipping %s as I was told to skip it" %(id)
            continue

        print "* Fetching raw message #%d of %d" % (id,count)
        raw_json = yga.messages(id, 'raw')
        mime = unescape_html(raw_json['rawEmail']).encode('latin_1', 'ignore')

        eml = email.message_from_string(mime)

        if (save or reattach) and message['hasAttachments']:
            atts = {}
            if not 'attachments' in message:
                print "** Yahoo says this message has attachments, but I can't find any!"
            else:
                for attach in message['attachments']:
                    print "** Fetching attachment '%s'" % (attach['filename'],)
                    if 'link' in attach:
                        atts[attach['filename']] = yga.get_file(attach['link'])
                    elif 'photoInfo' in attach:
                        photoinfo = get_best_photoinfo(attach['photoInfo'])
                        atts[attach['filename']] = yga.get_file(photoinfo['displayURL'])

                    if save:
                        attach_fname = "%s-%s" % (id, basename(attach['filename']))
                        with file(attach_fname, 'wb') as f:
                            f.write(atts[attach['filename']])

                if reattach:
                    for part in eml.walk():
                        part_fname = part.get_filename()
                        if part_fname and part_fname in atts:
                            part.set_payload(atts[part_fname])
                            email.encoders.encode_base64(part)
                            del atts[part_fname]

        with file(eml_fname, 'w') as f:
            f.write(eml.as_string(unixfrom=False))

def archive_files(yga, subdir=None, resume=False):
    if subdir:
        file_json = yga.files(sfpath=subdir)
    else:
        file_json = yga.files()

    with open('fileinfo.json', 'w') as f:
        f.write(json.dumps(file_json['dirEntries'], indent=4))

    n = 0
    sz = len(file_json['dirEntries'])
    for path in file_json['dirEntries']:
        n += 1
        if path['type'] == 0:
            # Regular file
            name = unescape_html(path['fileName'])
            if resume and os.path.isfile(name):
                print "* Skipping %s (%d/%d) as it already exists" % (name, n, sz)
                continue

            print "* Fetching file '%s' (%d/%d)" % (name, n, sz)
            with open(basename(name), 'wb') as f:
                yga.download_file(path['downloadURL'], f)

        elif path['type'] == 1:
            # Directory
            print "* Fetching directory '%s' (%d/%d)" % (path['fileName'], n, sz)
            with Mkchdir(basename(path['fileName']).replace('.', '')):
                pathURI = urllib.unquote(path['pathURI'])
                archive_files(yga, subdir=pathURI, resume=resume)

def archive_photos(yga, resume=False):
    albums = yga.albums()
    n = 0

    for a in albums['albums']:
        n += 1
        name = unescape_html(a['albumName'])
        # Yahoo has an off-by-one error in the album count...
        print "* Fetching album '%s' (%d/%d)" % (name, n, albums['total'] - 1)

        with Mkchdir(basename(name).replace('.', '')):
            photos = yga.albums(a['albumId'])
            p = 0

            for photo in photos['photos']:
                p += 1
                pname = unescape_html(photo['photoName'])

                photoinfo = get_best_photoinfo(photo['photoInfo'])
                fname = "%d-%s.jpg" % (photo['photoId'], basename(pname))

                if resume and os.path.isfile(fname):
                    print "** Skipping %s as it already exists" % (fname)
                    continue

                print "** Fetching photo '%s' (%d/%d)" % (pname, p, photos['total'])

                with open(fname, 'wb') as f:
                    yga.download_file(photoinfo['displayURL'], f)

def archive_db(yga, group, resume=False):
    json = yga.database()
    n = 0
    nts = len(json['tables'])
    for table in json['tables']:
        n += 1

        name = basename(table['name']) + '.csv'
        uri = "https://groups.yahoo.com/neo/groups/%s/database/%s/records/export?format=csv" % (group, table['tableId'])

        if resume and os.path.isfile(name):
            print "* Skipping %s as it already exists" % (name)
            continue

        print "* Downloading database table '%s' (%d/%d)" % (table['name'], n, nts)

        with open(name, 'w') as f:
            yga.download_file(uri, f)

def get_skiplist(skip):
    if not skip:
        return []
    return [int(i) for i in skip]

class Mkchdir:
    d = ""
    def __init__(self, d):
        self.d = d

    def __enter__(self):
        try:
            os.mkdir(self.d)
        except OSError:
            pass
        os.chdir(self.d)

    def __exit__(self, exc_type, exc_value, traceback):
        os.chdir('..')

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument('-u', '--username', type=str)
    p.add_argument('-p', '--password', type=str,
            help='If no password supplied, will be requested on the console')
    p.add_argument('-ct', '--cookie_t', type=str)
    p.add_argument('-cy', '--cookie_y', type=str)
    p.add_argument('-m', '--resume', action='store_true',
            help='Resume download from the cloud; skips messages already on local disk')
    p.add_argument('-k', '--skip', action='append',
            help='Message ids to skip; may specify multiple times')

    po = p.add_argument_group(title='What to archive', description='By default, all the below.')
    po.add_argument('-e', '--email', action='store_true',
            help='Only archive email and attachments')
    po.add_argument('-f', '--files', action='store_true',
            help='Only archive files')
    po.add_argument('-i', '--photos', action='store_true',
            help='Only archive photo galleries')
    po.add_argument('-d', '--database', action='store_true',
            help='Only archive database')

    pe = p.add_argument_group(title='Email Options')
    pe.add_argument('-r', '--no-reattach', action='store_true',
            help="Don't reattach attachment files to email")
    pe.add_argument('-s', '--no-save', action='store_true',
            help="Don't save email attachments as individual files")

    p.add_argument('group', type=str)

    args = p.parse_args()

    yga = YahooGroupsAPI(args.group, args.cookie_t, args.cookie_y)
    if args.username:
        password = args.password or getpass.getpass()
        print "logging in..."
        if not yga.login(args.username, password):
            print "Login failed"
            sys.exit(1)

    if not (args.email or args.files or args.photos or args.database):
        args.email = args.files = args.photos = args.database = True

    with Mkchdir(args.group):
        if args.email:
            with Mkchdir('email'):
                archive_email(yga, reattach=(not args.no_reattach), save=(not args.no_save),
                                    resume=args.resume, skip=get_skiplist(args.skip))
        if args.files:
            with Mkchdir('files'):
                archive_files(yga, resume=args.resume)
        if args.photos:
            with Mkchdir('photos'):
                archive_photos(yga, resume=args.resume)
        if args.database:
            with Mkchdir('databases'):
                archive_db(yga, args.group, resume=args.resume)
