<VirtualHost *:80>
  ServerAdmin @ADMIN_MAIL@
  ServerName @BINDING@
  Redirect permanent / https://@BINDING@/
  ErrorLog /var/log/apache2/@SITE@-80-error.log
</VirtualHost>

<VirtualHost *:443>
  ServerAdmin @ADMIN_MAIL@
  ServerName @BINDING@
  DocumentRoot @DOC_ROOT@

  AllowEncodedSlashes NoDecode
  SSLEngine On
  SSLCertificateFile @CERT_FILE@
  SSLCertificateKeyFile @KEY_FILE@

  ErrorLog /var/log/apache2/@SITE@-443-error.log
  <Directory @DOC_ROOT@>
    AllowOverride All
    Require all granted
  </Directory>
</VirtualHost>
