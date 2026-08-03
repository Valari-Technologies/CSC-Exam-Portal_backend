"""Add school_board and school_code fields to School."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('schools', '0003_alter_school_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='school',
            name='school_board',
            field=models.CharField(
                choices=[
                    ('state_board', 'State Board'),
                    ('cbse', 'CBSE Board'),
                    ('matriculation', 'Matriculation'),
                ],
                default='state_board',
                help_text='The academic board the school is affiliated with.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='school',
            name='school_code',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Official government-issued school code.',
                max_length=50,
            ),
        ),
    ]
