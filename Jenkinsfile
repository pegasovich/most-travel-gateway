pipeline {
    agent any

    environment {
        PYTHONUNBUFFERED = '1'
        MIN_COVERAGE = '85'
    }

    stages {
        stage('Checkout') {
            steps {
                echo 'Checking out source code...'
                checkout scm
            }
        }

        stage('Environment Setup') {
            steps {
                echo 'Setting up Python virtual environment and dependencies...'
                sh '''
                    python3 -m venv .venv
                    . .venv/bin/activate
                    pip install --upgrade pip
                    pip install -r requirements.txt
                '''
            }
        }

        stage('Security & Linting Gate') {
            steps {
                echo 'Executing Bandit (PCI-DSS Security) and Flake8 linter...'
                sh '''
                    . .venv/bin/activate
                    flake8 app tests --max-line-length=135 --ignore=E203,W503
                    bandit -r app -ll -ii
                '''
            }
        }

        stage('Automated QA Test Suite') {
            steps {
                echo 'Running Pytest async test suite with JUnit & Coverage reports...'
                sh '''
                    . .venv/bin/activate
                    mkdir -p reports
                    pytest -v \
                        --cov=app \
                        --cov-report=term-missing \
                        --cov-report=xml:reports/coverage.xml \
                        --junitxml=reports/junit.xml
                '''
            }
        }

        stage('Quality Gate Assertion') {
            steps {
                echo "Enforcing Quality Gate: Coverage must be >= ${env.MIN_COVERAGE}%"
                sh '''
                    . .venv/bin/activate
                    python -c "
import xml.etree.ElementTree as ET
import sys
tree = ET.parse('reports/coverage.xml')
rate = float(tree.getroot().attrib['line-rate']) * 100
print(f'Current Line Coverage: {rate:.2f}%')
if rate < float('${MIN_COVERAGE}'):
    print(f'Quality gate failed: Coverage {rate:.2f}% < ${MIN_COVERAGE}%')
    sys.exit(1)
print('Quality gate passed!')
"
                '''
            }
        }
    }

    post {
        always {
            echo 'Archiving test results and coverage metrics...'
            junit testResults: 'reports/junit.xml', allowEmptyResults: true
            archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true
        }
        success {
            echo 'MOST Travel Gateway CI Pipeline succeeded with all Quality Gates passing!'
        }
        failure {
            echo 'Pipeline failed! Alerting QA & Engineering team.'
        }
    }
}
