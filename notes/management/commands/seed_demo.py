import random

from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand

from notes.models import Comment, Note, Rating, Report, Subject
from notes.services import analyse_upload, apply_derived
from notes.signals import MODERATOR_GROUP, REVIEWER_GROUP

SUBJECTS = [
    ("Data Structures", 3, "CSE"), ("Operating Systems", 4, "CSE"),
    ("Database Management Systems", 4, "CSE"), ("Computer Networks", 5, "CSE"),
    ("Engineering Mathematics II", 2, "COMMON"), ("Digital Electronics", 3, "ECE"),
]
NOTES = [
    ("Trees and Graphs cheat sheet", 0, "unit-3, trees, graphs", "Traversals, complexity and common interview patterns."),
    ("Process scheduling worked examples", 1, "scheduling, midterm", "FCFS, SJF, RR with Gantt charts."),
    ("SQL joins and normalisation", 2, "sql, normalisation", "1NF to BCNF with examples."),
    ("TCP vs UDP revision", 3, "transport-layer", "Handshakes, congestion control, comparison table."),
    ("Laplace transform formulas", 4, "formulae, endsem", "One-page formula sheet."),
    ("K-map simplification practice", 5, "k-map, practice", "20 solved problems."),
    ("Sorting algorithms summary", 0, "sorting, unit-2", "Stability and complexity at a glance."),
    ("Deadlocks and memory management", 1, "deadlock, paging", "Banker's algorithm walk-through."),
]


# Longer text for one note so the duplicate-detection demo has real material to compare.
LONG_BODIES = {
    "Trees and Graphs cheat sheet": (
        "Trees and Graphs cheat sheet\n\n"
        "Binary tree traversals: inorder gives sorted output for a binary search tree, preorder copies a tree and "
        "postorder deletes one. Level order uses a queue. Balanced trees such as AVL and red-black keep height at "
        "log n so search, insert and delete stay logarithmic.\n\n"
        "Graph representations: an adjacency list uses O(V + E) space and suits sparse graphs, an adjacency matrix "
        "uses O(V squared) and gives constant time edge lookup. Breadth first search finds shortest paths in "
        "unweighted graphs, depth first search powers topological sort and strongly connected components.\n\n"
        "Shortest paths: Dijkstra needs non-negative weights, Bellman-Ford handles negative edges and detects "
        "negative cycles. Minimum spanning trees come from Kruskal with union find or Prim with a priority queue.\n"
    ),
}
NEAR_DUP_TITLE = "Trees and graphs quick revision"
PDF_TITLE = "Operating systems: scheduling and deadlock summary (PDF)"
PDF_PARAGRAPHS = [
    "Operating systems: scheduling and deadlock summary",
    "CPU scheduling decides which ready process runs next. First come first served is simple but suffers from "
    "the convoy effect. Shortest job first minimises average waiting time but needs burst estimates. Round robin "
    "gives each process a time quantum and favours interactive workloads.",
    "A deadlock needs four conditions at once: mutual exclusion, hold and wait, no pre-emption and circular wait. "
    "Banker's algorithm keeps the system in a safe state by granting a request only if a safe sequence still exists.",
    "Paging removes external fragmentation by splitting memory into fixed-size frames, and the page table maps "
    "virtual pages to frames. A TLB caches recent translations to keep the average access time low.",
]


def _pdf_bytes(paragraphs):
    """Build a small text PDF with PyMuPDF; returns None if PyMuPDF is unavailable."""
    try:
        import pymupdf
    except ImportError:
        return None
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(54, 54, 540, 790), "\n\n".join(paragraphs), fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


class Command(BaseCommand):
    help = "Load demo subjects, users, notes, ratings, comments, a near-duplicate and open reports."

    def add_note(self, title, subject, uploader, filename, data, tags, desc, downloads):
        """Create a note the same way an upload does: extract text, score duplicates, make a thumbnail."""
        f = ContentFile(data, name=filename)
        text, ranked = analyse_upload(f, subject)
        note = Note(title=title, subject=subject, uploader=uploader, tags=tags, description=desc, downloads=downloads)
        apply_derived(note, f, text, ranked)
        note.file.save(filename, ContentFile(data), save=False)
        note.save()
        return note

    def rate_and_comment(self, note, users):
        for u in users:
            if u != note.uploader and random.random() < 0.8:
                Rating.objects.create(note=note, user=u, stars=random.randint(3, 5))
        Comment.objects.create(note=note, user=random.choice([u for u in users if u != note.uploader]),
                               text="Really helpful, thanks for sharing!")

    def handle(self, *args, **opts):
        random.seed(7)
        subjects = [Subject.objects.get_or_create(name=n, semester=s, branch=b)[0] for n, s, b in SUBJECTS]

        users = []
        for name in ["asha", "ben", "chitra", "dev"]:
            u, made = User.objects.get_or_create(username=name)
            if made:
                u.set_password("peernotes123")
                u.save()
            users.append(u)

        mod, made = User.objects.get_or_create(username="moderator", defaults={"is_staff": True})
        if made:
            mod.set_password("peernotes123")
            mod.save()
        mod.groups.add(Group.objects.get_or_create(name=MODERATOR_GROUP)[0])

        reviewer, made = User.objects.get_or_create(username="reviewer")
        if made:
            reviewer.set_password("peernotes123")
            reviewer.save()
        reviewer.groups.add(Group.objects.get_or_create(name=REVIEWER_GROUP)[0])

        for i, (title, si, tags, desc) in enumerate(NOTES):
            if Note.objects.filter(title=title).exists():
                continue
            note = self.add_note(
                title, subjects[si], users[i % len(users)],
                f"{title.lower().replace(' ', '_')}.txt", LONG_BODIES.get(title, f"{title}\n\n{desc}\n").encode(),
                tags, desc, random.randint(0, 120))
            self.rate_and_comment(note, users)

        # Fill in text / thumbnails / scores for notes seeded by an earlier version of this command.
        call_command("backfill_notes", verbosity=0)

        # A near-duplicate of the first note, to demonstrate duplicate detection and the admin filter.
        original = Note.objects.filter(title=NOTES[0][0]).first()
        if original and not Note.objects.filter(title=NEAR_DUP_TITLE).exists():
            base = original.extracted_text or f"{original.title}\n\n{NOTES[0][3]}"
            note = self.add_note(
                NEAR_DUP_TITLE, original.subject, users[1] if original.uploader != users[1] else users[2],
                "trees_and_graphs_quick_revision.txt", (base.rstrip() + " Revised.\n").encode(),
                "unit-3, trees, graphs", "Same material as the cheat sheet with a couple of extra lines.", 3)
            self.rate_and_comment(note, users)
            self.stdout.write(f"Near-duplicate demo note created (similarity {note.similarity_score:.0%}).")

        # A PDF note, to demonstrate the in-page preview and the card thumbnail.
        pdf = _pdf_bytes(PDF_PARAGRAPHS)
        if pdf and not Note.objects.filter(title=PDF_TITLE).exists():
            note = self.add_note(PDF_TITLE, subjects[1], users[0], "os_scheduling_deadlock_summary.pdf", pdf,
                                 "scheduling, deadlock, paging", "One-page revision summary.", 18)
            self.rate_and_comment(note, users)

        self.seed_reports(users, mod)

        self.stdout.write(self.style.SUCCESS(
            "Demo data ready. Log in as asha/ben/chitra/dev, moderator or reviewer (password: peernotes123)."))

    def seed_reports(self, users, moderator):
        """A few open reports for the /moderation/ queue plus two handled ones for history."""
        plan = [
            (NEAR_DUP_TITLE, "duplicate", "Same content as the Trees and Graphs cheat sheet.", "open"),
            ("Laplace transform formulas", "wrong_subject", "This belongs under Engineering Mathematics, not ECE.", "open"),
            ("Sorting algorithms summary", "spam", "", "open"),
            ("TCP vs UDP revision", "other", "Table in the middle is cut off.", "resolved"),
            ("K-map simplification practice", "inappropriate", "", "dismissed"),
        ]
        for title, reason, details, status in plan:
            note = Note.objects.filter(title=title).first()
            if not note or Report.objects.filter(note=note, reason=reason).exists():
                continue
            reporter = next(u for u in users if u != note.uploader)
            report = Report.objects.create(note=note, reporter=reporter, reason=reason, details=details)
            if status != "open":
                report.close(moderator, status)
        self.stdout.write(f"Open reports in the queue: {Report.objects.filter(status='open').count()}.")
