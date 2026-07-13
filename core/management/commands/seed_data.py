"""
Management command to seed the database with sample test catalog data
and a superuser for development.
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User, Group
from core.models import TestCatalog, SampleType


SAMPLE_TESTS = [
    {
        'name': 'Complete Blood Count',
        'short_code': 'CBC',
        'sample_type': SampleType.BLOOD,
        'department': 'Hematology',
        'price': 350,
        'parameters': [
            {'name': 'Hemoglobin', 'unit': 'g/dL', 'ref_min': 12.0, 'ref_max': 17.5},
            {'name': 'WBC (Total)', 'unit': 'cells/μL', 'ref_min': 4000, 'ref_max': 11000},
            {'name': 'RBC', 'unit': 'million/μL', 'ref_min': 4.5, 'ref_max': 5.5},
            {'name': 'Platelets', 'unit': 'lakh/μL', 'ref_min': 1.5, 'ref_max': 4.0},
            {'name': 'PCV / Hematocrit', 'unit': '%', 'ref_min': 36, 'ref_max': 46},
            {'name': 'MCV', 'unit': 'fL', 'ref_min': 80, 'ref_max': 100},
            {'name': 'MCH', 'unit': 'pg', 'ref_min': 27, 'ref_max': 31},
            {'name': 'MCHC', 'unit': 'g/dL', 'ref_min': 32, 'ref_max': 36},
        ],
    },
    {
        'name': 'Liver Function Test',
        'short_code': 'LFT',
        'sample_type': SampleType.BLOOD,
        'department': 'Biochemistry',
        'price': 500,
        'parameters': [
            {'name': 'Total Bilirubin', 'unit': 'mg/dL', 'ref_min': 0.1, 'ref_max': 1.2},
            {'name': 'Direct Bilirubin', 'unit': 'mg/dL', 'ref_min': 0, 'ref_max': 0.3},
            {'name': 'SGOT (AST)', 'unit': 'U/L', 'ref_min': 5, 'ref_max': 40},
            {'name': 'SGPT (ALT)', 'unit': 'U/L', 'ref_min': 7, 'ref_max': 56},
            {'name': 'Alkaline Phosphatase', 'unit': 'U/L', 'ref_min': 44, 'ref_max': 147},
            {'name': 'Total Protein', 'unit': 'g/dL', 'ref_min': 6.0, 'ref_max': 8.3},
            {'name': 'Albumin', 'unit': 'g/dL', 'ref_min': 3.5, 'ref_max': 5.5},
        ],
    },
    {
        'name': 'Kidney Function Test',
        'short_code': 'KFT',
        'sample_type': SampleType.BLOOD,
        'department': 'Biochemistry',
        'price': 450,
        'parameters': [
            {'name': 'Blood Urea', 'unit': 'mg/dL', 'ref_min': 15, 'ref_max': 40},
            {'name': 'Serum Creatinine', 'unit': 'mg/dL', 'ref_min': 0.6, 'ref_max': 1.2},
            {'name': 'Uric Acid', 'unit': 'mg/dL', 'ref_min': 3.5, 'ref_max': 7.2},
            {'name': 'Sodium', 'unit': 'mEq/L', 'ref_min': 136, 'ref_max': 145},
            {'name': 'Potassium', 'unit': 'mEq/L', 'ref_min': 3.5, 'ref_max': 5.1},
            {'name': 'Calcium', 'unit': 'mg/dL', 'ref_min': 8.5, 'ref_max': 10.5},
        ],
    },
    {
        'name': 'Lipid Profile',
        'short_code': 'LIPID',
        'sample_type': SampleType.BLOOD,
        'department': 'Biochemistry',
        'price': 400,
        'parameters': [
            {'name': 'Total Cholesterol', 'unit': 'mg/dL', 'ref_min': 0, 'ref_max': 200},
            {'name': 'Triglycerides', 'unit': 'mg/dL', 'ref_min': 0, 'ref_max': 150},
            {'name': 'HDL Cholesterol', 'unit': 'mg/dL', 'ref_min': 40, 'ref_max': 60},
            {'name': 'LDL Cholesterol', 'unit': 'mg/dL', 'ref_min': 0, 'ref_max': 100},
            {'name': 'VLDL', 'unit': 'mg/dL', 'ref_min': 0, 'ref_max': 30},
        ],
    },
    {
        'name': 'Thyroid Profile',
        'short_code': 'TFT',
        'sample_type': SampleType.BLOOD,
        'department': 'Endocrinology',
        'price': 600,
        'parameters': [
            {'name': 'T3', 'unit': 'ng/dL', 'ref_min': 80, 'ref_max': 200},
            {'name': 'T4', 'unit': 'μg/dL', 'ref_min': 5.1, 'ref_max': 14.1},
            {'name': 'TSH', 'unit': 'μIU/mL', 'ref_min': 0.27, 'ref_max': 4.2},
        ],
    },
    {
        'name': 'Blood Sugar (Fasting)',
        'short_code': 'BSF',
        'sample_type': SampleType.BLOOD,
        'department': 'Biochemistry',
        'price': 100,
        'parameters': [
            {'name': 'Fasting Blood Sugar', 'unit': 'mg/dL', 'ref_min': 70, 'ref_max': 100},
        ],
    },
    {
        'name': 'Blood Sugar (PP)',
        'short_code': 'BSPP',
        'sample_type': SampleType.BLOOD,
        'department': 'Biochemistry',
        'price': 100,
        'parameters': [
            {'name': 'Post Prandial Blood Sugar', 'unit': 'mg/dL', 'ref_min': 70, 'ref_max': 140},
        ],
    },
    {
        'name': 'HbA1c',
        'short_code': 'HBA1C',
        'sample_type': SampleType.BLOOD,
        'department': 'Biochemistry',
        'price': 500,
        'parameters': [
            {'name': 'HbA1c', 'unit': '%', 'ref_min': 4.0, 'ref_max': 5.6},
        ],
    },
    {
        'name': 'Urine Routine / Microscopy',
        'short_code': 'URM',
        'sample_type': SampleType.URINE,
        'department': 'Pathology',
        'price': 150,
        'parameters': [
            {'name': 'Color', 'unit': '', 'ref_min': None, 'ref_max': None},
            {'name': 'Appearance', 'unit': '', 'ref_min': None, 'ref_max': None},
            {'name': 'pH', 'unit': '', 'ref_min': 4.6, 'ref_max': 8.0},
            {'name': 'Specific Gravity', 'unit': '', 'ref_min': 1.005, 'ref_max': 1.030},
            {'name': 'Protein', 'unit': '', 'ref_min': None, 'ref_max': None},
            {'name': 'Sugar', 'unit': '', 'ref_min': None, 'ref_max': None},
            {'name': 'Pus Cells', 'unit': '/hpf', 'ref_min': 0, 'ref_max': 5},
            {'name': 'RBC', 'unit': '/hpf', 'ref_min': 0, 'ref_max': 2},
            {'name': 'Epithelial Cells', 'unit': '/hpf', 'ref_min': None, 'ref_max': None},
        ],
    },
    {
        'name': 'Semen Analysis',
        'short_code': 'SEMEN',
        'sample_type': SampleType.SEMEN,
        'department': 'Pathology',
        'price': 400,
        'parameters': [
            {'name': 'Volume', 'unit': 'mL', 'ref_min': 1.5, 'ref_max': None},
            {'name': 'Liquefaction Time', 'unit': 'min', 'ref_min': None, 'ref_max': 30},
            {'name': 'pH', 'unit': '', 'ref_min': 7.2, 'ref_max': 8.0},
            {'name': 'Sperm Count', 'unit': 'million/mL', 'ref_min': 15, 'ref_max': None},
            {'name': 'Motility (Progressive)', 'unit': '%', 'ref_min': 32, 'ref_max': None},
            {'name': 'Morphology (Normal)', 'unit': '%', 'ref_min': 4, 'ref_max': None},
        ],
    },
    {
        'name': 'ESR',
        'short_code': 'ESR',
        'sample_type': SampleType.BLOOD,
        'department': 'Hematology',
        'price': 100,
        'parameters': [
            {'name': 'ESR (Westergren)', 'unit': 'mm/hr', 'ref_min': 0, 'ref_max': 20},
        ],
    },
    {
        'name': 'Vitamin D (25-OH)',
        'short_code': 'VITD',
        'sample_type': SampleType.BLOOD,
        'department': 'Biochemistry',
        'price': 800,
        'parameters': [
            {'name': '25-OH Vitamin D', 'unit': 'ng/mL', 'ref_min': 30, 'ref_max': 100},
        ],
    },
    {
        'name': 'Vitamin B12',
        'short_code': 'VITB12',
        'sample_type': SampleType.BLOOD,
        'department': 'Biochemistry',
        'price': 700,
        'parameters': [
            {'name': 'Vitamin B12', 'unit': 'pg/mL', 'ref_min': 200, 'ref_max': 900},
        ],
    },
    {
        'name': 'Widal Test',
        'short_code': 'WIDAL',
        'sample_type': SampleType.BLOOD,
        'department': 'Serology',
        'price': 250,
        'parameters': [
            {'name': 'S. Typhi O', 'unit': 'titre', 'ref_min': None, 'ref_max': None},
            {'name': 'S. Typhi H', 'unit': 'titre', 'ref_min': None, 'ref_max': None},
            {'name': 'S. Paratyphi AH', 'unit': 'titre', 'ref_min': None, 'ref_max': None},
            {'name': 'S. Paratyphi BH', 'unit': 'titre', 'ref_min': None, 'ref_max': None},
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed the database with test catalog, sample users, and groups'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset', action='store_true',
            help='Delete existing test catalog entries before seeding'
        )

    def handle(self, *args, **options):
        if options['reset']:
            TestCatalog.objects.all().delete()
            self.stdout.write(self.style.WARNING('Deleted existing test catalog entries'))

        # Seed test catalog
        created_count = 0
        for test_data in SAMPLE_TESTS:
            _, created = TestCatalog.objects.get_or_create(
                short_code=test_data['short_code'],
                defaults=test_data,
            )
            if created:
                created_count += 1

        self.stdout.write(self.style.SUCCESS(f'Test catalog: {created_count} tests created'))

        # Create sample users if they don't exist
        users_created = []
        user_configs = [
            ('admin', 'admin', True, []),
            ('reception', 'reception123', False, ['reception']),
            ('chamber', 'chamber123', False, ['chamber']),
            ('collector', 'collector123', False, ['collection']),
            ('labtech', 'labtech123', False, ['lab']),
        ]

        for username, password, is_super, group_names in user_configs:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'is_staff': True,
                    'is_superuser': is_super,
                }
            )
            if created:
                user.set_password(password)
                user.save()
                for gn in group_names:
                    try:
                        group = Group.objects.get(name=gn)
                        user.groups.add(group)
                    except Group.DoesNotExist:
                        pass
                users_created.append(f'{username} (password: {password})')

        if users_created:
            self.stdout.write(self.style.SUCCESS('Users created:'))
            for u in users_created:
                self.stdout.write(f'  - {u}')
        else:
            self.stdout.write(self.style.WARNING('All users already exist'))

        self.stdout.write(self.style.SUCCESS('\nSeed complete!'))
