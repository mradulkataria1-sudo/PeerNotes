"""Shared helpers for the test modules (not collected as tests)."""
import io
import shutil
import tempfile

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import Note, Subject

PASSWORD = "pw12345!"

TREES = (
    "Binary tree traversals: inorder gives sorted output for a binary search tree, preorder copies a tree and "
    "postorder deletes one. Level order uses a queue. Balanced trees such as AVL and red-black keep the height "
    "logarithmic so search, insert and delete stay fast. Graph representations: an adjacency list uses linear "
    "space and suits sparse graphs while an adjacency matrix gives constant time edge lookup."
)
TREES_NEAR_COPY = TREES + " Revised."
DEADLOCK = (
    "A deadlock needs mutual exclusion, hold and wait, no pre-emption and circular wait to hold at the same time. "
    "The banker's algorithm grants a resource request only when a safe sequence of processes still exists. "
    "Paging removes external fragmentation by mapping virtual pages onto fixed-size frames."
)


HASHING = (
    "Hash tables map keys to buckets with a hash function. Collisions are resolved by chaining or by open "
    "addressing with linear probing, quadratic probing or double hashing. The load factor controls resizing."
)


def make_pdf(text, pages=1):
    import pymupdf

    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(54, 54, 540, 790), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def make_docx(text):
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def upload(name, data):
    return SimpleUploadedFile(name, data if isinstance(data, bytes) else data.encode())


class MediaTestCase(TestCase):
    """TestCase with a throw-away MEDIA_ROOT, users, a subject and a clean throttle cache."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        cls._media = override_settings(
            MEDIA_ROOT=cls._tmp,
            PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],  # fast test users
        )
        cls._media.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media.disable()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.subject = Subject.objects.create(name="DSA", semester=3, branch="CSE")
        self.other_subject = Subject.objects.create(name="OS", semester=4, branch="CSE")
        self.alice = User.objects.create_user("alice", password=PASSWORD)
        self.bob = User.objects.create_user("bob", password=PASSWORD)
        self.carol = User.objects.create_user("carol", password=PASSWORD)
        self.moderator = User.objects.create_user("mod_user", password=PASSWORD)
        self.moderator.groups.add(Group.objects.get(name="Moderators"))
        self.reviewer = User.objects.create_user("rev_user", password=PASSWORD)
        self.reviewer.groups.add(Group.objects.get(name="Reviewers"))

    def login(self, user):
        self.client.login(username=user.username, password=PASSWORD)

    def make_note(self, title, user=None, subject=None, filename=None, data=b"hello", text="", **kw):
        return Note.objects.create(
            title=title, subject=subject or self.subject, uploader=user or self.alice,
            file=upload(filename or f"{title}.txt", data), extracted_text=text, **kw,
        )
