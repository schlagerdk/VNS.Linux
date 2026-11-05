<VirtualHost *:80>
  ServerAdmin @ADMIN_MAIL@
  ServerName @BINDING@
  DocumentRoot @DOC_ROOT@

  RewriteEngine On
  RewriteCond %{REQUEST_URI} !^/.well-known/acme-challenge/
  RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]

  ErrorLog /var/log/apache2/@SITE@-rewrite-error.log
</VirtualHost>

<VirtualHost *:443>
  ServerAdmin @ADMIN_MAIL@
  ServerName @BINDING@
  DocumentRoot @DOC_ROOT@

  AllowEncodedSlashes NoDecode

  ProxyPreserveHost On
  ProxyRequests Off
  ProxyPass /.well-known/acme-challenge/ !
  ProxyPass / http://127.0.0.1:@PORT_HTTP@/ nocanon
  ProxyPassReverse / http://127.0.0.1:@PORT_HTTP@/

  SSLEngine On
  SSLCertificateFile @CERT_FILE@
  SSLCertificateKeyFile @KEY_FILE@

  ErrorLog /var/log/apache2/@SITE@-443-error.log
</VirtualHost>
