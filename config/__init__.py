"""Use PyMySQL as the MySQL driver when mysqlclient is not installed (shared hosting / cPanel)."""
try:
    import MySQLdb  # noqa: F401
except ImportError:
    try:
        import pymysql

        pymysql.version_info = (2, 2, 1, "final", 0)  # satisfy Django's mysqlclient version check
        pymysql.install_as_MySQLdb()
    except ImportError:
        pass
