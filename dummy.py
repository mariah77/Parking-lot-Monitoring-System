from datetime import date

@app.route('/livestream')
def livestream():
    return render_template('livestream.html')

@app.route('/dashboard', methods=['GET'])
def hello_world():
    extract_data()
    today = date.today()
    today = today.strftime('%Y-%m-%d')
    print(today)
    print(type(today))
    for d in cars_count:
        print(type(d.get("date")))
    return render_template('dashboard.html',cars_count=cars_count,today=today)