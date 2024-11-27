from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, time, date
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from collections import defaultdict
from flask import make_response
from xhtml2pdf import pisa
from io import BytesIO

app = Flask(__name__)
app.secret_key = "chave_secreta"

# Configuração do banco de dados
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///barbearia.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Modelo de Usuário
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(15), nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

# Modelo de Agendamento
class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.Time, nullable=False)
    user = db.relationship('User', backref='appointments')
    service_type = db.Column(db.String(100), nullable=False)  # Tipo de serviço
    price = db.Column(db.Float, nullable=False)  # Preço do serviço
    __table_args__ = (
        db.UniqueConstraint('date', 'time', name='unique_appointment'),
    )

# Decorator para verificar login
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Por favor, faça login para acessar a agenda.")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route("/admin/relatorio", methods=["GET", "POST"])
@login_required
def gerar_relatorio():
    user_id = session['user_id']
    user = User.query.get(user_id)

    if not user.is_admin:
        flash("Acesso negado. Apenas administradores podem acessar esta página.", "error")
        return redirect(url_for("home"))

    if request.method == "POST":
        report_type = request.form.get("report_type")  # 'day' ou 'month'
        if report_type == "day":
            # Relatório Diário
            selected_date = request.form.get("date")
            if not selected_date:
                flash("Por favor, selecione uma data.", "error")
                return redirect(url_for("relatorio_page"))

            report_date = datetime.strptime(selected_date, "%Y-%m-%d").date()
            appointments = Appointment.query.filter_by(date=report_date).all()
            title = f"Relatório do Dia: {report_date.strftime('%d/%m/%Y')}"

        elif report_type == "month":
            # Relatório Mensal
            selected_month = request.form.get("month")
            if not selected_month:
                flash("Por favor, selecione um mês.", "error")
                return redirect(url_for("relatorio_page"))

            report_year, report_month = map(int, selected_month.split("-"))
            appointments = Appointment.query.filter(
                db.extract("year", Appointment.date) == report_year,
                db.extract("month", Appointment.date) == report_month
            ).all()
            title = f"Relatório do Mês: {datetime(report_year, report_month, 1).strftime('%B %Y')}"

        else:
            flash("Selecione um tipo de relatório válido.", "error")
            return redirect(url_for("relatorio_page"))

        # Calcular o total de ganhos
        total_earnings = sum([appointment.price for appointment in appointments])

        # Gerar PDF ou exibir o relatório
        rendered_html = render_template(
            "relatorio.html",
            appointments=appointments,
            total_earnings=total_earnings,
            title=title
        )

        pdf = BytesIO()
        pisa_status = pisa.CreatePDF(BytesIO(rendered_html.encode("UTF-8")), pdf)

        if pisa_status.err:
            flash("Erro ao gerar o relatório em PDF.", "error")
            return redirect(url_for("relatorio_page"))

        pdf.seek(0)
        response = make_response(pdf.read())
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = f"inline; filename=relatorio.pdf"
        return response

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/relatorio-page")
@login_required
def relatorio_page():
    user_id = session['user_id']
    user = User.query.get(user_id)

    if not user.is_admin:
        flash("Acesso negado. Apenas administradores podem acessar esta página.", "error")
        return redirect(url_for("home"))

    return render_template("relatorio_page.html")


@app.route("/painel")
@login_required
def painel():
    # Exibe todos os agendamentos ordenados por data e hora
    appointments = Appointment.query.join(User).order_by(Appointment.date, Appointment.time).all()
    return render_template("painel.html", appointments=appointments)


@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    user_id = session['user_id']
    user = User.query.get(user_id)

    if not user.is_admin:
        flash("Acesso negado. Apenas administradores podem acessar esta página.", "error")
        return redirect(url_for("home"))

    # Obter o dia atual
    today = date.today()

    # Organizar por mês e por dia
    appointments = Appointment.query.order_by(Appointment.date, Appointment.time).all()
    appointments_today = []
    appointments_by_day = defaultdict(list)
    appointments_by_month = defaultdict(list)

    for appointment in appointments:
        # Separar os agendamentos de hoje
        if appointment.date == today:
            appointments_today.append(appointment)

        # Agrupar por dia
        day = appointment.date.strftime('%d/%m/%Y')
        appointments_by_day[day].append(appointment)

        # Agrupar por mês
        month = appointment.date.strftime('%B %Y')
        appointments_by_month[month].append(appointment)

    return render_template(
        "admin_dashboard.html",
        appointments_today=appointments_today,
        appointments_by_month=appointments_by_month,
        appointments_by_day=appointments_by_day,
        view_mode=request.args.get('view', 'month'),  # Padrão: 'month'
        today=today  # Adicionando o dia atual
    )


# Página inicial
@app.route("/")
def home():
    return render_template("index.html")

# Página de login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        # Verificar se todos os campos foram preenchidos
        if not email or not password:
            flash("Todos os campos são obrigatórios.", "error")
            return redirect(url_for("login"))

        # Buscar o usuário pelo e-mail
        user = User.query.filter_by(email=email).first()

        # Validar o usuário e a senha diretamente (sem hash)
        if user and user.password == password:
            session['user_id'] = user.id

            # Redirecionar para o dashboard se for administrador
            if user.is_admin:
                return redirect(url_for("admin_dashboard"))

            # Redirecionar para a página inicial se for um usuário comum
            return redirect(url_for("home"))

        # Caso as credenciais estejam incorretas
        flash("Email ou senha inválidos.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")



# Página de registro
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        # Verificar se as senhas coincidem
        if password != confirm_password:
            flash('As senhas não coincidem.', 'error')
            return redirect(url_for('register'))

        # Verificar se o e-mail já está registrado
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('Este e-mail já está em uso.', 'error')
            return redirect(url_for('register'))

        # Criar um novo usuário
        
        new_user = User(name=name, email=email, phone=phone, password=password)

        db.session.add(new_user)
        db.session.commit()

        flash('Conta criada com sucesso! Faça login para continuar.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route("/admin/editar-agendamento/<int:appointment_id>", methods=["GET", "POST"])
def editar_agendamento(appointment_id):
    # Buscar o agendamento pelo ID
    appointment = Appointment.query.get_or_404(appointment_id)

    if request.method == "POST":
        try:
            # Converter data e hora do formulário para objetos datetime.date e datetime.time
            new_date = datetime.strptime(request.form.get("date"), "%Y-%m-%d").date()
            new_time = datetime.strptime(request.form.get("time"), "%H:%M").time()

            # Atualizar os campos do agendamento
            appointment.date = new_date
            appointment.time = new_time
            appointment.service_type = request.form.get("service_type")
            appointment.price = float(request.form.get("price"))

            # Salvar no banco de dados
            db.session.commit()
            flash("Agendamento atualizado com sucesso!", "success")
            return redirect(url_for("admin_dashboard"))

        except Exception as e:
            flash(f"Erro ao atualizar o agendamento: {str(e)}", "error")
            return redirect(url_for("editar_agendamento", appointment_id=appointment_id))

    return render_template("editar_agendamento.html", appointment=appointment)

@app.route("/admin/deletar-agendamento/<int:appointment_id>", methods=["GET", "POST"])
def deletar_agendamento(appointment_id):
    # Buscar o agendamento pelo ID
    appointment = Appointment.query.get_or_404(appointment_id)

    try:
        # Remover o agendamento do banco de dados
        db.session.delete(appointment)
        db.session.commit()
        flash("Agendamento deletado com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao deletar o agendamento: {str(e)}", "error")

    # Redirecionar de volta ao dashboard
    return redirect(url_for("admin_dashboard"))


# Página de agendamento (protegida)
@app.route("/agendar", methods=["GET", "POST"])
@login_required
def agendar():
    if request.method == "POST":
        user_id = session['user_id']
        selected_date = request.form.get("date")
        selected_time = request.form.get("time")
        service_type = request.form.get("service_type")

        if not selected_date or not selected_time:
            flash("Data e horário são obrigatórios.", "error")
            return redirect(url_for("agendar"))

        appointment_date = datetime.strptime(selected_date, "%Y-%m-%d").date()
        appointment_time = datetime.strptime(selected_time, "%H:%M").time()

        if appointment_date < date.today():
            flash("Não é possível agendar em datas passadas.", "error")
            return redirect(url_for("agendar"))

        existing_appointment = Appointment.query.filter_by(
            date=appointment_date, time=appointment_time
        ).first()

        if existing_appointment:
            flash("Este horário já está reservado.", "error")
            return redirect(url_for("agendar"))
        
         # Determinar o preço com base no tipo de serviço
        prices = {
            "Corte Simples": 30.00,
            "Corte Completo": 50.00,
            "Barba": 20.00,
            "Corte e Barba": 70.00,
        }
        price = prices.get(service_type, 0.00)

        # Criação de novo agendamento
        new_appointment = Appointment(
            user_id=user_id, date=appointment_date, time=appointment_time,service_type=service_type, price=price
        )
        db.session.add(new_appointment)
        db.session.commit()

        #flash("Agendamento realizado com sucesso!", "success")
        return redirect(url_for("meus_agendamentos"))

    # Mostrar horários disponíveis
    today = date.today()
    available_times = [
        (time(hour, 0), time(hour + 1, 0)) for hour in range(9, 18)
    ]

    appointments = Appointment.query.filter(Appointment.date >= today).all()
    booked_times = {(a.date, a.time) for a in appointments}

    return render_template(
        "agendar.html",
        available_times=available_times,
        booked_times=booked_times,
    )

@app.route("/meus_agendamentos")
@login_required
def meus_agendamentos():
    user_id = session['user_id']
    appointments = Appointment.query.filter_by(user_id=user_id).order_by(Appointment.date, Appointment.time).all()
    return render_template("meus_agendamentos.html", appointments=appointments)


# Rota para logout
@app.route("/logout")
def logout():
    session.pop('user_id', None)
    return redirect(url_for('home'))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    # Verificar e criar o usuário administrador, se não existir
        if not User.query.filter_by(email="admin@barbearia.com").first():
            admin_user = User(
                name="Admin",
                email="admin@barbearia.com",
                phone="(00) 00000-0000",
                password=("admin123"),
                is_admin=True
            )
            db.session.add(admin_user)
            db.session.commit()
            

    app.run(debug=True, host='0.0.0.0')
