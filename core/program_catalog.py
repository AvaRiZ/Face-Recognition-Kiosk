DEFAULT_COLLEGE_PROGRAM_MAP = {
    "College of Engineering": [
        "Bachelor of Science in Chemical Engineering",
        "Bachelor of Science in Food Engineering",
        "Bachelor of Science in Ceramics Engineering",
        "Bachelor of Science in Metallurgical Engineering",
        "Bachelor of Science in Civil Engineering",
        "Bachelor of Science in Sanitary Engineering",
        "Bachelor of Science in Geodetic Engineering",
        "Bachelor of Science in Geological Engineering",
        "Bachelor of Science in Transportation Systems Engineering",
        "Bachelor of Science in Electrical Engineering",
        "Bachelor of Science in Computer Engineering",
        "Bachelor of Science in Electronics Engineering",
        "Bachelor of Science in Instrumentation and Control Engineering",
        "Bachelor of Science in Mechatronics Engineering",
        "Bachelor of Science in Aerospace Engineering",
        "Bachelor of Science in Biomedical Engineering",
        "Bachelor of Science in Industrial Engineering",
        "Bachelor of Science in Mechanical Engineering",
        "Bachelor of Science in Petroleum Engineering",
        "Bachelor of Science in Automotive Engineering",
    ],
    "College of Architecture, Fine Arts and Design": [
        "Bachelor of Fine Arts and Design Major in Visual Communication",
        "Bachelor of Science in Architecture",
        "Bachelor of Science in Interior Design",
    ],
    "College of Arts and Sciences": [
        "Bachelor of Arts in English Language Studies",
        "Bachelor of Arts in Communication",
        "Bachelor of Science in Biology",
        "Bachelor of Science in Chemistry",
        "Bachelor of Science in Criminology",
        "Bachelor of Science in Development Communication",
        "Bachelor of Science in Mathematics",
        "Bachelor of Science in Psychology",
        "Bachelor of Science in Fisheries and Aquatic Sciences",
    ],
    "College of Accountancy, Business, Economics, and International Hospitality Management": [
        "Bachelor of Science in Accountancy",
        "Bachelor of Science in Business Administration Major in Business Economics",
        "Bachelor of Science in Business Administration Major in Financial Management",
        "Bachelor of Science in Business Administration Major in Human Resource Management",
        "Bachelor of Science in Business Administration Major in Marketing Management",
        "Bachelor of Science in Business Administration Major in Operations Management",
        "Bachelor of Science in Hospitality Management",
        "Bachelor of Science in Tourism Management",
        "Bachelor in Public Administration",
        "Bachelor of Science in Customs Administration",
        "Bachelor of Science in Entrepreneurship",
    ],
    "College of Informatics and Computing Sciences": [
        "Bachelor of Science in Computer Science",
        "Bachelor of Science in Information Technology",
    ],
    "College of Nursing and Allied Health Sciences": [
        "Bachelor of Science in Nursing",
        "Bachelor of Science in Nutrition and Dietetics",
        "Bachelor of Science in Public Health (Disaster Response)",
    ],
    "College of Engineering Technology": [
        "Bachelor of Automotive Engineering Technology",
        "Bachelor of Civil Engineering Technology",
        "Bachelor of Computer Engineering Technology",
        "Bachelor of Drafting Engineering Technology",
        "Bachelor of Electrical Engineering Technology",
        "Bachelor of Electronics Engineering Technology",
        "Bachelor of Food Engineering Technology",
        "Bachelor of Instrumentation and Control Engineering Technology",
        "Bachelor of Mechanical Engineering Technology",
        "Bachelor of Mechatronics Engineering Technology",
        "Bachelor of Welding and Fabrication Engineering Technology",
    ],
    "College of Agriculture and Forestry": [
        "Bachelor of Science in Agriculture",
        "Bachelor of Science in Forestry",
    ],
    "College of Teacher Education": [
        "Bachelor of Elementary Education",
        "Bachelor of Early Childhood Education",
        "Bachelor of Secondary Education Major in Science",
        "Bachelor of Secondary Education Major in English",
        "Bachelor of Secondary Education Major in Filipino",
        "Bachelor of Secondary Education Major in Mathematics",
        "Bachelor of Secondary Education Major in Social Studies",
        "Bachelor of Technology & Livelihood Education Major in Home Economics",
        "Bachelor of Technical-Vocational Teacher Education Major in Garments, Fashion and Design",
        "Bachelor of Technical-Vocational Teacher Education Major in Electronics Technology",
        "Bachelor of Physical Education",
    ],
}

OTHER_COLLEGE_LABEL = "Other / Unassigned"


def iter_program_catalog():
    for department_name, programs in DEFAULT_COLLEGE_PROGRAM_MAP.items():
        for program_name in programs:
            yield department_name, program_name

