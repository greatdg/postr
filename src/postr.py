#! /usr/bin/python

# Postr, a Flickr Uploader
#
# Copyright (C) 2006 Ross Burton <ross@burtonini.com>
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51 Franklin
# St, Fifth Floor, Boston, MA 02110-1301 USA

import os, threading
from Queue import Queue
from urlparse import urlparse

import pygtk; pygtk.require ("2.0")
import gobject, gtk, gtk.glade
gobject.threads_init()

import EXIF
from flickrapi import FlickrAPI

# My top secret Flickr API keys
flickrAPIKey = "c53cebd15ed936073134cec858036f1d"
flickrSecret = "7db1b8ef68979779"

# TODO: do this in a thread or something to stop blocking
fapi = FlickrAPI(flickrAPIKey, flickrSecret)
token = fapi.getToken(browser="firefox", perms="write")

# Constants for the drag handling
(DRAG_URI,
 DRAG_IMAGE) = range (0, 2)

# Column indexes
(COL_FILENAME, # The filename of an image (can be None)
 COL_IMAGE, # The image data (if filename is None)
 COL_PREVIEW, # A 512x512 preview of the image
 COL_THUMBNAIL, # A 64x64 thumbnail of the image
 COL_TITLE, # The image title
 COL_DESCRIPTION, # The image description
 COL_TAGS # A space deliminated list of tags for the image
 ) = range (0, 7)


# The task queue to transfer jobs to the upload thread
upload_queue = Queue()
uploading = threading.Event()

# TODO: split out
_abbrevs = [
    (1<<50L, 'P'),
    (1<<40L, 'T'), 
    (1<<30L, 'G'), 
    (1<<20L, 'M'), 
    (1<<10L, 'k'),
    (1, '')
    ]

def greek(size):
    for factor, suffix in _abbrevs:
        if size > factor:
            break
    return "%.1f%s" % (float(size)/factor, suffix)


# A cunning decorator to thread an arbitrary method.  See
# http://www.oreillynet.com/onlamp/blog/2006/07/pygtk_and_threading.html
def threaded(f):
    def wrapper(*args):
        t = threading.Thread(target=f, args=args)
        t.setDaemon(True)
        t.start()
    return wrapper

class AboutDialog(gtk.AboutDialog):
    def __init__(self, parent):
        gtk.AboutDialog.__init__(self)
        self.set_transient_for(parent)
        self.set_name('Flickr Uploader')
        self.set_copyright(u'Copyright \u00A9 2006 Ross Burton')
        self.set_authors(('Ross Burton <ross@burtonini.com>',))
        self.set_website('http://burtonini.com/')


class Postr:
    def __init__(self):
        glade = gtk.glade.XML(os.path.join (os.path.dirname(__file__), "postr.glade"))
        glade.signal_autoconnect(self)

        self.window = glade.get_widget("main_window")
        # Just for you, Daniel.
        if os.getlogin() == "daniels":
            self.window.set_title("Respecognise")
        
        self.statusbar = glade.get_widget("statusbar")

        self.thumbnail_image = glade.get_widget("thumbnail_image")
        self.title_entry = glade.get_widget("title_entry")
        self.desc_entry = glade.get_widget("desc_entry")
        self.tags_entry = glade.get_widget("tags_entry")

        self.model = gtk.ListStore (gobject.TYPE_STRING,
                                    gtk.gdk.Pixbuf,
                                    gtk.gdk.Pixbuf,
                                    gtk.gdk.Pixbuf,
                                    gobject.TYPE_STRING,
                                    gobject.TYPE_STRING,
                                    gobject.TYPE_STRING)
        self.current_it = None

        self.title_entry.connect('changed', self.on_field_changed, COL_TITLE)
        self.desc_entry.connect('changed', self.on_field_changed, COL_DESCRIPTION)
        self.tags_entry.connect('changed', self.on_field_changed, COL_TAGS)
        self.thumbnail_image.connect('size-allocate', self.update_thumbnail)
        self.old_thumb_allocation = None

        self.iconview = glade.get_widget("iconview")
        self.iconview.set_model (self.model)
        self.iconview.set_text_column (COL_TITLE)
        self.iconview.set_pixbuf_column (COL_THUMBNAIL)
    
        self.iconview.drag_dest_set (gtk.DEST_DEFAULT_ALL, (), gtk.gdk.ACTION_COPY)
        targets = ()
        targets = gtk.target_list_add_image_targets(targets, DRAG_IMAGE, False)
        targets = gtk.target_list_add_uri_targets(targets, DRAG_URI)
        self.iconview.drag_dest_set_target_list(targets)

        # TODO: probably need some sort of lock to stop multiple threads
        self.get_quota()

    @threaded
    def get_quota(self):
        rsp = fapi.people_getUploadStatus(api_key=flickrAPIKey, auth_token=token)
        if fapi.getRspErrorCode(rsp) != 0:
            # TODO: fire error dialog or ignore
            print fapi.getPrintableError(rsp)
        else:
            gtk.gdk.threads_enter()
            bandwidth = rsp.user[0].bandwidth[0]
            context = self.statusbar.get_context_id("quota")
            self.statusbar.pop(context)
            self.statusbar.push(context, "You have %s remaining this month" %
                                greek(int(bandwidth['remainingbytes'])))
            gtk.gdk.threads_leave()

    def on_field_changed(self, entry, column):
        items = self.iconview.get_selected_items()
        for path in items:
            it = self.model.get_iter(path)
            self.model.set_value (it, column, entry.get_text())
    
    def on_add_photos_activate(self, menuitem):
        # TODO: add preview widget
        dialog = gtk.FileChooserDialog(title="Add Photos", parent=self.window,
                                       action=gtk.FILE_CHOOSER_ACTION_OPEN,
                                       buttons=(gtk.STOCK_CANCEL,
                                                gtk.RESPONSE_CANCEL,
                                                gtk.STOCK_OPEN,
                                                gtk.RESPONSE_OK))
        dialog.set_select_multiple(True)
        
        filters = gtk.FileFilter()
        filters.set_name("Images")
        filters.add_mime_type("image/png")
        filters.add_mime_type("image/jpeg")
        filters.add_mime_type("image/gif")
        dialog.add_filter(filters)
        filters = gtk.FileFilter()
        filters.set_name("All Files")
        filters.add_pattern("*")
        dialog.add_filter(filters)

        if dialog.run() != gtk.RESPONSE_OK:
            return
        
        files = dialog.get_filenames()
        dialog.destroy()
        
        for f in files:
            self.add_image_filename(f)
    
    def on_quit_activate(self, menuitem):
        if uploading.isSet():
            # TODO: if there are pending uploads, confirm first
            print "Uploading, should query user"
        gtk.main_quit()
    
    def on_delete_activate(self, menuitem):
        selection = self.iconview.get_selected_items()
        for path in selection:
            self.model.remove(self.model.get_iter(path))
    
    def on_select_all_activate(self, menuitem):
        self.iconview.select_all()

    def on_deselect_all_activate(self, menuitem):
        self.iconview.unselect_all()

    def on_invert_selection_activate(self, menuitem):
        selected = self.iconview.get_selected_items()
        def inverter(model, path, iter, selected):
            if path in selected:
                self.iconview.unselect_path(path)
            else:
                self.iconview.select_path(path)
        self.model.foreach(inverter, selected)

    def on_upload_activate(self, menuitem):
        it = self.model.get_iter_first()
        # If we have some pictures, disable the iconview
        if it is not None:
            # TODO: disable upload menu item
            self.iconview.set_sensitive(False)
        
        while it is not None:
            (filename, pixbuf, title, desc, tags) = self.model.get(it,
                                                              COL_FILENAME,
                                                              COL_IMAGE,
                                                              COL_TITLE,
                                                              COL_DESCRIPTION,
                                                              COL_TAGS)
    
            t = UploadTask()
            t.filename = filename
            t.pixbuf = pixbuf
            t.title = title
            t.description = desc
            t.tags = tags
    
            upload_queue.put(t)
            
            it = self.model.iter_next(it)
    
    def on_about_activate(self, menuitem):
        dialog = AboutDialog(self.window)
        dialog.run()
        dialog.destroy()

    def update_thumbnail(self, widget, allocation = None):
        if self.current_it:
            if not allocation:
                allocation = widget.get_allocation()
                force = True
            else:
                force = False

            # hrngh.  seemingly a size-allocate call (with identical params,
            # mind) gets called every time we call set_from_pixbuf.  even if
            # we connect it to the window.  so very braindead.
            if not force and self.old_thumb_allocation and \
               self.old_thumb_allocation.width == allocation.width and \
               self.old_thumb_allocation.height == allocation.height:
                return;

            self.old_thumb_allocation = allocation

            (image, simage, filename) = self.model.get(self.current_it,
                                                       COL_IMAGE,
                                                       COL_PREVIEW,
                                                       COL_FILENAME)

            tw = allocation.width
            th = allocation.height
            # Clamp the size to 512
            if tw > 512: tw = 512
            if th > 512: th = 512
            (tw, th) = self.get_thumb_size(simage.get_width(),
                                           simage.get_height(),
                                           tw, th)

            
            thumb = simage.scale_simple(tw, th, gtk.gdk.INTERP_BILINEAR)
            widget.set_from_pixbuf(thumb)

    def on_selection_changed(self, iconview):
        items = iconview.get_selected_items()

        def enable_field(field, text):
            field.set_sensitive(True)
            field.set_text(text)
        def disable_field(field):
            field.set_sensitive(False)
            field.set_text("")
        
        if items:
            # TODO: do something clever with multiple selections
            self.current_it = self.model.get_iter(items[0])
            (title, desc, tags) = self.model.get(self.current_it,
                                                 COL_TITLE,
                                                 COL_DESCRIPTION,
                                                 COL_TAGS)

            enable_field(self.title_entry, title)
            enable_field(self.desc_entry, desc)
            enable_field(self.tags_entry, tags)

            self.update_thumbnail(self.thumbnail_image)
        else:
            self.current_it = None
            disable_field(self.title_entry)
            disable_field(self.desc_entry)
            disable_field(self.tags_entry)

            self.thumbnail_image.set_from_pixbuf(None)

    @staticmethod
    def get_thumb_size(srcw, srch, dstw, dsth):
        ratio = srcw/float(srch)
        if srcw > srch:
            return (dstw, int(dstw/ratio))
        else:
            return (int(dsth*ratio), dsth)

    def add_image_filename(self, filename):
        # TODO: MIME type check

        # On a file that doesn't contain EXIF, like a PNG, this just returns an
        # empty set.
        exif = EXIF.process_file(open(filename, 'rb'))

        # TODO: rotate if required

        # First we load the image scaled to 512x512 for the preview.
        preview = gtk.gdk.pixbuf_new_from_file_at_size(filename, 512, 512)

        # Now scale the preview to a thumbnail
        sizes = self.get_thumb_size(preview.get_width(), preview.get_height(), 64, 64)
        thumb = preview.scale_simple(sizes[0], sizes[1], gtk.gdk.INTERP_BILINEAR)
    
        # Extra useful data from image
        title = os.path.splitext(os.path.basename(filename))[0] # TODO: more
        desc = exif.get('Image ImageDescription', "")
        
        self.model.set(self.model.append(),
                       COL_FILENAME, filename,
                       COL_IMAGE, None,
                       COL_PREVIEW, preview,
                       COL_THUMBNAIL, thumb,
                       COL_TITLE, title,
                       COL_DESCRIPTION, desc,
                       COL_TAGS, "")
    
    def on_drag_data_received(self, widget, context, x, y, selection, targetType, timestamp):
        if targetType == DRAG_IMAGE:
            pixbuf = selection.get_pixbuf()

            # TODO: don't scale up if the image is smaller than 512/512
            
            # Scale the pixbuf to a preview
            sizes = self.get_thumb_size (pixbuf.get_width(), pixbuf.get_height(), 512, 512)
            preview = pixbuf.scale_simple(sizes[0], sizes[1], gtk.gdk.INTERP_BILINEAR)
            # Now scale to a thumbnail
            sizes = self.get_thumb_size (pixbuf.get_width(), pixbuf.get_height(), 64, 64)
            thumb = pixbuf.scale_simple(sizes[0], sizes[1], gtk.gdk.INTERP_BILINEAR)
            
            self.model.set(self.model.append(),
                           COL_IMAGE, pixbuf,
                           COL_FILENAME, None,
                           COL_PREVIEW, preview,
                           COL_THUMBNAIL, thumb,
                           COL_TITLE, "",
                           COL_DESCRIPTION, "",
                           COL_TAGS, "")
        
        elif targetType == DRAG_URI:
            for uri in selection.get_uris():
                # TODO: use gnome-vfs to handle remote files
                filename = urlparse(uri)[2]
                if os.path.isfile(filename):
                    self.add_image_filename(filename)
                elif os.path.isdir(filename):
                    for root, dirs, files in os.walk(filename):
                        for f in files:
                            # TODO: handle symlinks to directories as they are
                            # in files
                            self.add_image_filename (os.path.join(root, f))
                else:
                    print "Unhandled file %s" % filename
                    
        else:
            print "Unhandled target type %d" % targetType
        
        context.finish(True, True, timestamp)

    def done(self):
        self.model.clear()
        self.iconview.set_sensitive(True)
        # TODO: enable upload menu item
        self.get_quota()
    

# TODO: replace this mojo with @threaded
class UploadTask:
    uri = None
    pixbuf = None
    title = None
    description = None
    tags = None


class Uploader(threading.Thread):
    def __init__(self, postr):
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self.postr = postr
        
    def run(self):
        while 1:
            # This blocks when the queue is empty
            t = upload_queue.get()
            uploading.set()
            
            # TODO: construct a list and pass that to avoid duplication
            if t.filename:
                ret = fapi.upload(api_key=flickrAPIKey, auth_token=token,
                                  filename=t.filename,
                                  title=t.title, description=t.description, tags=t.tags)
            elif t.pixbuf:
                # This isn't very nice, but might be the best way
                data = []
                t.pixbuf.save_to_callback(lambda d: data.append(d), "png", {})
                ret = fapi.upload(api_key=flickrAPIKey, auth_token=token,
                                  imageData=''.join(data),
                                  title=t.title, description=t.description, tags=t.tags)
            else:
                print "No data in task"
                continue
            
            if fapi.getRspErrorCode(ret) != 0:
                # TODO: fire error dialog
                print fapi.getPrintableError(ret)

            if upload_queue.empty():
                uploading.clear()
                def done(postr):
                    postr.done()
                    return False
                gobject.idle_add(done, self.postr)


if __name__ == "__main__":
    p = Postr()
    Uploader(p).start()
    p.window.show()
    gtk.main()
