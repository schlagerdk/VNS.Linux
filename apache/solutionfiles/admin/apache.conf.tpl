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

  Header always set Strict-Transport-Security "max-age=31536000"
  Header always set X-Robots-Tag "noindex, nofollow"

  <Location />
    @ACCESS_RULES@
  </Location>

  ProxyPreserveHost On
  ProxyRequests Off
  ProxyPass /.well-known/acme-challenge/ !
  ProxyPass /ws/ ws://127.0.0.1:@PORT_HTTP@/ws/
  ProxyPassReverse /ws/ ws://127.0.0.1:@PORT_HTTP@/ws/
  ProxyPass / http://127.0.0.1:@PORT_HTTP@/ nocanon
  ProxyPassReverse / http://127.0.0.1:@PORT_HTTP@/
  RequestHeader set X-Forwarded-Proto "https"

  SSLEngine On
  SSLCertificateFile @CERT_FILE@
  SSLCertificateKeyFile @KEY_FILE@

  ErrorLog /var/log/apache2/@SITE@-443-error.log
</VirtualHost>
