<VirtualHost *:80>
  ServerAdmin @ADMIN_MAIL@
  ServerName @BINDING@
  DocumentRoot @DOC_ROOT@
  ErrorLog /var/log/apache2/@SITE@-80-error.log
  <Directory @DOC_ROOT@>
    AllowOverride All
    Require all granted
  </Directory>
</VirtualHost>
