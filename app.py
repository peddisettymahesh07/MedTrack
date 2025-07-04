from flask import Flask, request, session, redirect, url_for, render_template, flash
import boto3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
import os
from dotenv import load_dotenv
# ---------------------------------------
# Flask App Initialization
# ---------------------------------------
load_dotenv()  
app = Flask(__name__)
app.secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

# ---------------------------------------
# App Configuration (Inline) 
# ---------------------------------------
AWS_REGION_NAME = 'us-east-1'  # Example: Mumbai region

# Email config (use App Passwords if Gmail)
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587
SENDER_EMAIL = '228x1a42a1@khitguntur.ac.in'  # Replace with your sender email
SENDER_PASSWORD = '@Pmahesh1982'    # Replace with your email app password
ENABLE_EMAIL = True

# ---------------------------------------
# AWS Resources Using IAM Role (No Keys)
# ---------------------------------------
dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION_NAME)
sns = boto3.client('sns', region_name=AWS_REGION_NAME)

# ---------------------------------------
# DynamoDB Tables (Replace with your table names)
# ---------------------------------------
user_table = dynamodb.Table('UsersTable')
appointment_table = dynamodb.Table('AppointmentsTable')

# ---------------------------------------
# Logging
# ---------------------------------------
logging.basicConfig(level=logging.INFO)

# ---------------------------------------
# Helper Functions
# ---------------------------------------

# Check if user is logged in
def is_logged_in():
    return 'email' in session

# Get user role from DynamoDB
def get_user_role(email):
    try:
        response = user_table.get_item(Key={'email': email})
        if 'Item' in response:
            return response['Item'].get('role')
    except Exception as e:
        app.logger.error(f"Error fetching role: {e}")
    return None

# Send email notifications
def send_email(to_email, subject, body):
    if not ENABLE_EMAIL:
        app.logger.info(f"[Email Skipped] Subject: {subject} to {to_email}")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()

        app.logger.info(f"Email sent to {to_email}")
    except Exception as e:
        app.logger.error(f"Email failed: {e}")

# -------------------------------
# Routes
# -------------------------------

# Home Page
@app.route('/')
def index():
    if is_logged_in():
        return redirect(url_for('dashboard'))
    return render_template('index.html')

# Register User (Doctor/Patient)
@app.route('/register', methods=['GET', 'POST'])
def register():
    if is_logged_in():  # Check if already logged in
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        # Form validation
        required_fields = ['name', 'email', 'password', 'age', 'gender', 'role']
        for field in required_fields:
            if field not in request.form or not request.form[field]:
                flash(f'Please fill in the {field} field', 'danger')
                return render_template('register.html')
        
        # Check if passwords match
        if 'confirm_password' in request.form and request.form['password'] != request.form['confirm_password']:
            flash('Passwords do not match', 'danger')
            return render_template('register.html')
        
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])  # Hash password
        age = request.form['age']
        gender = request.form['gender']
        role = request.form['role']  # 'doctor' or 'patient'
        
        # Check if user already exists
        existing_user = user_table.get_item(Key={'email': email}).get('Item')
        if existing_user:
            flash('Email already registered', 'danger')
            return render_template('register.html')

        # Add user to DynamoDB
        user_item = {
            'email': email,
            'name': name,
            'password': password,  # Store hashed password
            'age': age,
            'gender': gender,
            'role': role,
            'login_count': 0,  # Track login count
            'created_at': datetime.now().isoformat(),
        }
        
        # Add specialization only for doctors
        if role == 'doctor' and 'specialization' in request.form:
            user_item['specialization'] = request.form['specialization']
        
        user_table.put_item(Item=user_item)
        
        # Send welcome email if enabled
        if app.config.get('ENABLE_EMAIL', False):
            welcome_msg = f"Welcome to HealthCare App, {name}! Your account has been created successfully."
            send_email(email, "Welcome to HealthCare App", welcome_msg)
        
        # Send admin notification via SNS if configured
        if app.config.get('SNS_TOPIC_ARN'):
            try:
                sns.publish(
                    TopicArn=app.config.get('SNS_TOPIC_ARN'),
                    Message=f'New user registered: {name} ({email}) as {role}',
                    Subject='New User Registration - HealthCare App'
                )
            except Exception as e:
                app.logger.error(f"Failed to publish to SNS: {e}")
        
        flash('Registration successful. Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

# Login User (Doctor/Patient)
@app.route('/login', methods=['GET', 'POST'])
def login():
    if is_logged_in():  # If the user is already logged in, redirect to dashboard
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        if not request.form.get('email') or not request.form.get('password') or not request.form.get('role'):
            flash('All fields are required', 'danger')
            return render_template('login.html')
            
        email = request.form['email']
        password = request.form['password']
        role = request.form['role']  # Get the selected role (doctor or patient)

        # Validate user credentials
        user = user_table.get_item(Key={'email': email}).get('Item')

        if user:
            # Check password and role
            if check_password_hash(user['password'], password):  # Use check_password_hash to verify hashed password
                if user['role'] == role:
                    session['email'] = email
                    session['role'] = role  # Store the role in the session
                    session['name'] = user.get('name', '')
                    
                    # Update login count
                    try:
                        user_table.update_item(
                            Key={'email': email},
                            UpdateExpression='SET login_count = if_not_exists(login_count, :zero) + :inc',
                            ExpressionAttributeValues={':inc': 1, ':zero': 0}
                        )
                    except Exception as e:
                        app.logger.error(f"Failed to update login count: {e}")
                    
                    flash('Login successful.', 'success')
                    return redirect(url_for('dashboard'))
                else:
                    flash('Invalid role selected.', 'danger')
            else:
                flash('Invalid password.', 'danger')
        else:
            flash('Email not found.', 'danger')

    return render_template('login.html')

# Logout User
@app.route('/logout')
def logout():
    session.pop('email', None)
    session.pop('role', None)
    session.pop('name', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))

# Dashboard for both Doctors and Patients
@app.route('/dashboard')
def dashboard():
    if not is_logged_in():
        flash('Please log in to continue.', 'danger')
        return redirect(url_for('login'))

    role = session['role']
    email = session['email']

    if role == 'doctor':
        # Show doctor dashboard with list of appointments
        try:
            response = appointment_table.scan(
                FilterExpression="#doctor_email = :email",
                ExpressionAttributeNames={"#doctor_email": "doctor_email"},
                ExpressionAttributeValues={":email": email}
            )
            appointments = response['Items']
        except Exception as e:
            app.logger.error(f"Failed to fetch appointments: {e}")
            appointments = []
        return render_template('doctor_dashboard.html', appointments=appointments)

    elif role == 'patient':
        # Show patient dashboard with list of their appointments
        try:
            response = appointment_table.scan(
                FilterExpression="#patient_email = :email",
                ExpressionAttributeNames={"#patient_email": "patient_email"},
                ExpressionAttributeValues={":email": email}
            )
            appointments = response['Items']
            
            # Get list of doctors for booking new appointments
            doctor_response = user_table.scan(
                FilterExpression="#role = :role",
                ExpressionAttributeNames={"#role": "role"},
                ExpressionAttributeValues={":role": 'doctor'}
            )
            doctors = doctor_response['Items']
        except Exception as e:
            app.logger.error(f"Failed to fetch data: {e}")
            appointments = []
            doctors = []
        
        return render_template('patient_dashboard.html', appointments=appointments, doctors=doctors)

# Book an Appointment (Patient)
@app.route('/book_appointment', methods=['GET', 'POST'])
def book_appointment():
    if not is_logged_in() or session['role'] != 'patient':
        flash('Only patients can book appointments.', 'danger')
        return redirect(url_for('login'))

    if request.method == 'POST':
        # Form validation
        if not request.form.get('doctor_email') or not request.form.get('symptoms'):
            flash('Please fill all required fields', 'danger')
            return redirect(url_for('book_appointment'))
            
        doctor_email = request.form['doctor_email']
        symptoms = request.form['symptoms']
        appointment_date = request.form.get('appointment_date', datetime.now().isoformat())
        patient_email = session['email']
        
        # Get patient and doctor information for notifications
        try:
            patient = user_table.get_item(Key={'email': patient_email}).get('Item', {})
            doctor = user_table.get_item(Key={'email': doctor_email}).get('Item', {})
            
            patient_name = patient.get('name', 'Patient')
            doctor_name = doctor.get('name', 'Doctor')

            # Create a new appointment
            appointment_id = str(uuid.uuid4())
            appointment_item = {
                'appointment_id': appointment_id,
                'doctor_email': doctor_email,
                'doctor_name': doctor_name,
                'patient_email': patient_email,
                'patient_name': patient_name,
                'symptoms': symptoms,
                'status': 'pending',
                'appointment_date': appointment_date,
                'created_at': datetime.now().isoformat(),
            }
            
            appointment_table.put_item(Item=appointment_item)
            
            # Send email notifications if enabled
            if app.config.get('ENABLE_EMAIL', False):
                # Send email notification to doctor
                doctor_msg = f"Dear Dr. {doctor_name},\n\nA new appointment has been booked by {patient_name}.\n\nSymptoms: {symptoms}\nDate: {appointment_date}\n\nPlease login to your dashboard to view details."
                send_email(doctor_email, "New Appointment Notification", doctor_msg)
                
                # Send confirmation email to patient
                patient_msg = f"Dear {patient_name},\n\nYour appointment with Dr. {doctor_name} has been booked successfully.\n\nDate: {appointment_date}\n\nThank you for using our healthcare service."
                send_email(patient_email, "Appointment Confirmation", patient_msg)
            
            flash('Appointment booked successfully.', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            app.logger.error(f"Failed to book appointment: {e}")
            flash('An error occurred while booking the appointment. Please try again.', 'danger')
            return redirect(url_for('book_appointment'))

    # Get list of doctors for selection
    try:
        response = user_table.scan(
            FilterExpression="#role = :role",
            ExpressionAttributeNames={"#role": "role"},
            ExpressionAttributeValues={":role": 'doctor'}
        )
        doctors = response['Items']
    except Exception as e:
        app.logger.error(f"Failed to fetch doctors: {e}")
        doctors = []
    return render_template('book_appointment.html', doctors=doctors)

# View Appointment (Doctor)
@app.route('/view_appointment/<appointment_id>', methods=['GET', 'POST'])
def view_appointment(appointment_id):
    if not is_logged_in():
        flash('Please log in to continue.', 'danger')
        return redirect(url_for('login'))

    # Fetch appointment details
    try:
        response = appointment_table.get_item(Key={'appointment_id': appointment_id})
        appointment = response.get('Item')
        
        if not appointment:
            flash('Appointment not found.', 'danger')
            return redirect(url_for('dashboard'))
        
        # Security check - verify the logged-in user should access this appointment
        if session['role'] == 'doctor' and appointment['doctor_email'] != session['email']:
            flash('You are not authorized to view this appointment.', 'danger')
            return redirect(url_for('dashboard'))
        elif session['role'] == 'patient' and appointment['patient_email'] != session['email']:
            flash('You are not authorized to view this appointment.', 'danger')
            return redirect(url_for('dashboard'))

        if request.method == 'POST' and session['role'] == 'doctor':
            diagnosis = request.form['diagnosis']
            treatment_plan = request.form['treatment_plan']
            prescription = request.form['prescription']

            # Update appointment with doctor's diagnosis and treatment plan
            appointment_table.update_item(
                Key={'appointment_id': appointment_id},
                UpdateExpression="set diagnosis = :d, treatment_plan = :t, prescription = :p, #s = :s, updated_at = :u",
                ExpressionAttributeValues={
                    ':d': diagnosis,
                    ':t': treatment_plan,
                    ':p': prescription,
                    ':s': 'completed',
                    ':u': datetime.now().isoformat()
                },
                ExpressionAttributeNames={
                    '#s': 'status'  # Use #s as an alias for the reserved keyword 'status'
                }
            )
            
            # Send email notification to patient if enabled
            if app.config.get('ENABLE_EMAIL', False):
                patient_email = appointment['patient_email']
                patient_name = appointment.get('patient_name', 'Patient')
                doctor_name = appointment.get('doctor_name', 'your doctor')
                
                patient_msg = f"Dear {patient_name},\n\nYour appointment with Dr. {doctor_name} has been completed.\n\nDiagnosis: {diagnosis}\n\nTreatment Plan: {treatment_plan}\n\nPrescription: {prescription}\n\nPlease login to your dashboard for more details."
                send_email(patient_email, "Appointment Completed - Diagnosis Available", patient_msg)
            
            flash('Diagnosis submitted successfully.', 'success')
            return redirect(url_for('dashboard'))

        # Determine which template to render based on user role
        if session['role'] == 'doctor':
            return render_template('view_appointment_doctor.html', appointment=appointment)
        else:  # patient
            return render_template('view_appointment_patient.html', appointment=appointment)
    except Exception as e:
        app.logger.error(f"Error in view_appointment: {e}")
        flash('An error occurred. Please try again.', 'danger')
        return redirect(url_for('dashboard'))

# Search functionality for appointments
@app.route('/search_appointments', methods=['GET', 'POST'])
def search_appointments():
    if not is_logged_in():
        flash('Please log in to continue.', 'danger')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        search_term = request.form.get('search_term', '')
        
        try:
            if session['role'] == 'doctor':
                # Doctors can search their patients by name
                response = appointment_table.scan(
                    FilterExpression="#doctor_email = :email AND contains(#patient_name, :search)",
                    ExpressionAttributeNames={
                        "#doctor_email": "doctor_email",
                        "#patient_name": "patient_name"
                    },
                    ExpressionAttributeValues={
                        ":email": session['email'],
                        ":search": search_term
                    }
                )
            else:  # patient
                # Patients can search their appointments by doctor name or status
                response = appointment_table.scan(
                    FilterExpression="#patient_email = :email AND (contains(#doctor_name, :search) OR contains(#status, :search))",
                    ExpressionAttributeNames={
                        "#patient_email": "patient_email",
                        "#doctor_name": "doctor_name",
                        "#status": "status"
                    },
                    ExpressionAttributeValues={
                        ":email": session['email'],
                        ":search": search_term
                    }
                )
            
            appointments = response['Items']
            return render_template('search_results.html', appointments=appointments, search_term=search_term)
        except Exception as e:
            app.logger.error(f"Search failed: {e}")
            flash('Search failed. Please try again.', 'danger')
    
    return redirect(url_for('dashboard'))

# User profile page
@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if not is_logged_in():
        flash('Please log in to continue.', 'danger')
        return redirect(url_for('login'))
    
    email = session['email']
    try:
        user = user_table.get_item(Key={'email': email}).get('Item', {})
        
        if request.method == 'POST':
            # Update user profile
            name = request.form.get('name')
            age = request.form.get('age')
            gender = request.form.get('gender')
            
            update_expression = "SET #name = :name, age = :age, gender = :gender"
            expression_values = {
                ':name': name,
                ':age': age,
                ':gender': gender
            }
            
            # Update specialization only for doctors
            if session['role'] == 'doctor' and 'specialization' in request.form:
                update_expression += ", specialization = :spec"
                expression_values[':spec'] = request.form['specialization']
            
            user_table.update_item(
                Key={'email': email},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
                ExpressionAttributeNames={'#name': 'name'}
            )
            
            # Update session name
            session['name'] = name
            
            flash('Profile updated successfully.', 'success')
            return redirect(url_for('profile'))
        
        return render_template('profile.html', user=user)
    except Exception as e:
        app.logger.error(f"Profile error: {e}")
        flash('An error occurred. Please try again.', 'danger')
        return redirect(url_for('dashboard'))

# Submit Diagnosis (Doctor) - For form submission
@app.route('/submit_diagnosis/<appointment_id>', methods=['POST'])
def submit_diagnosis(appointment_id):
    if not is_logged_in() or session['role'] != 'doctor':
        flash('Only doctors can submit diagnoses.', 'danger')
        return redirect(url_for('login'))
    
    try:
        # Verify doctor is assigned to this appointment
        response = appointment_table.get_item(Key={'appointment_id': appointment_id})
        appointment = response.get('Item')
        
        if not appointment or appointment['doctor_email'] != session['email']:
            flash('You are not authorized to submit diagnosis for this appointment.', 'danger')
            return redirect(url_for('dashboard'))
        
        diagnosis = request.form['diagnosis']
        treatment_plan = request.form['treatment_plan']
        prescription = request.form['prescription']

        # Update the appointment in the database
        appointment_table.update_item(
            Key={'appointment_id': appointment_id},
            UpdateExpression="SET diagnosis = :d, treatment_plan = :t, prescription = :p, #status = :s, updated_at = :u",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ':d': diagnosis,
                ':t': treatment_plan,
                ':p': prescription,
                ':s': 'completed',
                ':u': datetime.now().isoformat()
            }
        )
        
        # Send email notification to patient if enabled
        if app.config.get('ENABLE_EMAIL', False):
            patient_email = appointment['patient_email']
            patient_name = appointment.get('patient_name', 'Patient')
            doctor_name = session.get('name', 'your doctor')
            
            patient_msg = f"Dear {patient_name},\n\nYour appointment with Dr. {doctor_name} has been completed.\n\nDiagnosis: {diagnosis}\n\nTreatment Plan: {treatment_plan}\n\nPrescription: {prescription}\n\nPlease login to your dashboard for more details."
            send_email(patient_email, "Appointment Completed - Diagnosis Available", patient_msg)

        flash('Diagnosis submitted successfully.', 'success')
        return redirect(url_for('dashboard'))
    except Exception as e:
        app.logger.error(f"Submit diagnosis error: {e}")
        flash('An error occurred while submitting the diagnosis. Please try again.', 'danger')
        return redirect(url_for('view_appointment', appointment_id=appointment_id))

# -------------------------------
# Run the Flask app
# -------------------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))  # Get port from environment or default to 5000
    app.run(host='0.0.0.0', port=port, debug=True)

