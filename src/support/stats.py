from pprint import pprint
import json
import os
import pandas as pd


class Stats:
    def __init__(self):
        self.projects = {}
        self.attachments = {
            "testrail": 0,
            "qase": 0
        }
        self.users = {
            "testrail": 0,
            "qase": 0
        }
        self.custom_fields = {
            "testrail": 0,
            "qase": 0
        }

    def add_project(self, code: str, title: str):
        self.projects[code] = {
            "title": title,
            "testrail": {
                "suites": 0,
                "cases": 0,
                "runs": 0,
                "milestones": 0,
                "shared_steps": 0,
                "configurations": 0,
            },
            "qase": {
                "suites": 0,
                "cases": 0,
                "runs": 0,
                "milestones": 0,
                "shared_steps": 0,
                "configurations": 0,
            }
        }

    def add_user(self, type: str, count: int = 1):
        self.users[type] += count
        
    def add_attachment(self, type: str, count: int = 1):
        self.attachments[type] += count

    def add_custom_field(self, type: str, count: int = 1):
        self.custom_fields[type] += count

    def add_entity_count(self, code: str, entity: str, type: str, count: int = 1):
        self.projects[code][type][entity] += count

    def print(self):
        print("------ Stats ------")
        print()
        pprint(vars(self), depth=4, sort_dicts=False)

    def save(self, prefix: str = ''):
        filename = f'{prefix}_stats.json'
        stats_dir = './stats'
        if not os.path.exists(stats_dir):
            os.makedirs(stats_dir)
        stats_file = os.path.join(stats_dir, f'{filename}')
        with open(stats_file, 'w') as f:
            json.dump(vars(self), f, indent=4)

    def save_xlsx(self, prefix: str = ''):
        try:
            filename = f'{prefix}_stats.xlsx'
            stats_dir = './stats'
            if not os.path.exists(stats_dir):
                os.makedirs(stats_dir)
            stats_file = os.path.join(stats_dir, f'{filename}')

            # Prepare data for comparison. This example assumes `self.projects` needs to be compared.
            # You can adjust the logic based on what exactly you need to compare.
            data_for_comparison = {
                'Project Code': [],
                'Title': [],
                'Entity': [],
                'Qase': [],
                'TestRail': []
            }

            for code, project in self.projects.items():
                for entity in ['suites', 'cases', 'runs', 'milestones', 'shared_steps', 'configurations']:
                    # Append project code and title for each entity to maintain equal list lengths
                    data_for_comparison['Project Code'].append(code)
                    data_for_comparison['Title'].append(project['title'])
                    data_for_comparison['Entity'].append(entity)
                    data_for_comparison['Qase'].append(project['qase'][entity])
                    data_for_comparison['TestRail'].append(project['testrail'][entity])
            
            df = pd.DataFrame(data_for_comparison)

            # Writing data to Excel with pandas
            with pd.ExcelWriter(stats_file, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Comparison')
        except Exception as e:
            return