"""Test data factories — build a valid object graph for the exam→result flow.

Plain helper functions (no factory_boy dependency, which isn't installed).
A module-level counter keeps codes/emails unique across the process; the DB is
rolled back per TestCase so there are never real collisions.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.academics.models import Chapter, Class, Section, Subject
from apps.authentication.models import User
from apps.exams.models import ExamSession
from apps.questions.models import Question
from apps.results.models import Result
from apps.schools.models import School
from apps.students.models import StudentProfile
from apps.tests.models import Test, TestAssignment, TestQuestion

# Passwords must satisfy the project's 12-char complexity validator.
DEFAULT_PASSWORD = "Csc@2026Test!"

_counter = {"n": 0}


def _uid() -> int:
    _counter["n"] += 1
    return _counter["n"]


def make_school(**kwargs) -> School:
    n = _uid()
    defaults = dict(
        name=f"School {n}",
        code=f"SCH{n:04d}",
        address="1 Test Road",
        city="Chennai",
        state="Tamil Nadu",
        pincode="600001",
        official_email=f"school{n}@example.com",
        contact_phone="9000000000",
    )
    defaults.update(kwargs)
    return School.objects.create(**defaults)


def make_user(
    role: str, school: School | None, password: str | None = DEFAULT_PASSWORD, **kwargs
) -> User:
    n = _uid()
    email = kwargs.pop("email", f"{role}{n}@example.com")
    full_name = kwargs.pop("full_name", f"{role.title()} {n}")
    return User.objects.create_user(
        email=email,
        password=password,
        full_name=full_name,
        role=role,
        school=school,
        **kwargs,
    )


def make_class(school: School, numeric_value: int = 10) -> Class:
    n = _uid()
    return Class.objects.create(
        school=school, name=f"Class {numeric_value}-{n}", numeric_value=numeric_value
    )


def make_section(school: School, school_class: Class, name: str | None = None) -> Section:
    n = _uid()
    return Section.objects.create(school=school, school_class=school_class, name=name or f"S{n}")


def make_subject(school: School, school_class: Class, name: str | None = None) -> Subject:
    n = _uid()
    return Subject.objects.create(
        school=school, school_class=school_class, name=name or f"Subject {n}"
    )


def make_chapter(subject: Subject) -> Chapter:
    n = _uid()
    return Chapter.objects.create(subject=subject, name=f"Chapter {n}")


def make_student(school: School, school_class: Class, section: Section, **kwargs) -> User:
    """Create a student User plus their StudentProfile; returns the User."""
    user = make_user("student", school, **kwargs)
    n = _uid()
    StudentProfile.objects.create(
        user=user,
        school=school,
        school_class=school_class,
        section=section,
        roll_number=f"R{n}",
    )
    return user


def make_question(
    school: School,
    subject: Subject,
    chapter: Chapter,
    correct_option: str = "a",
    marks=Decimal("2"),
    negative_marks=Decimal("0"),
    created_by: User | None = None,
) -> Question:
    return Question.objects.create(
        school=school,
        subject=subject,
        chapter=chapter,
        created_by=created_by,
        question_text="2 + 2 = ?",
        option_a="4",
        option_b="3",
        option_c="5",
        option_d="6",
        correct_option=correct_option,
        marks=marks,
        negative_marks=negative_marks,
    )


def make_test(
    school: School,
    subject: Subject,
    school_class: Class,
    created_by: User,
    questions: list[Question] | None = None,
    status: str = Test.Status.PUBLISHED,
    duration_minutes: int = 30,
    passing_marks=Decimal("2"),
) -> Test:
    questions = questions or []
    total = sum((q.marks for q in questions), Decimal("0"))
    test = Test.objects.create(
        school=school,
        created_by=created_by,
        title=f"Test {_uid()}",
        subject=subject,
        school_class=school_class,
        duration_minutes=duration_minutes,
        total_marks=total,
        passing_marks=passing_marks,
        status=status,
    )
    for i, q in enumerate(questions, start=1):
        TestQuestion.objects.create(test=test, question=q, order_number=i)
    return test


def make_assignment(
    test: Test,
    school_class: Class,
    assigned_by: User,
    assigned_to_type: str = TestAssignment.AssignedToType.CLASS,
    section: Section | None = None,
) -> TestAssignment:
    now = timezone.now()
    return TestAssignment.objects.create(
        test=test,
        assigned_to_type=assigned_to_type,
        school_class=school_class,
        section=section,
        start_datetime=now - timedelta(hours=1),
        end_datetime=now + timedelta(days=1),
        assigned_by=assigned_by,
    )


def make_session(
    student: User,
    assignment: TestAssignment,
    test: Test,
    status: str = ExamSession.Status.SUBMITTED,
    time_remaining_seconds: int = 0,
) -> ExamSession:
    return ExamSession.objects.create(
        student=student,
        assignment=assignment,
        test=test,
        time_remaining_seconds=time_remaining_seconds,
        status=status,
    )


def make_result(
    session: ExamSession, obtained=Decimal("2"), total=Decimal("2"), is_published: bool = False
) -> Result:
    pct = (obtained / total * 100) if total else Decimal("0")
    return Result.objects.create(
        session=session,
        student=session.student,
        test=session.test,
        assignment=session.assignment,
        total_marks=total,
        obtained_marks=obtained,
        percentage=round(pct, 2),
        passed=obtained >= session.test.passing_marks,
        time_taken_seconds=60,
        is_published=is_published,
    )
