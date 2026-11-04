<VirtualHost *:80>
  ServerAdmin @ADMIN_MAIL@
  ServerName @SITE@
  DocumentRoot @DOC_ROOT@

  RewriteEngine On
  RewriteCond %{REQUEST_URI} !^/.well-known/acme-challenge/
  RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]

  ErrorLog /var/log/apache2/@SITE@-rewrite-error.log
</VirtualHost>

<VirtualHost *:443>
  ServerAdmin @ADMIN_MAIL@
  ServerName @SITE@
  DocumentRoot @DOC_ROOT@

  AllowEncodedSlashes NoDecode

  RewriteEngine On
  RewriteCond %{HTTP:Upgrade} =websocket [NC]
  RewriteCond %{HTTP:Connection} upgrade [NC]
  RewriteRule /(.*) ws://127.0.0.1:@PORT_HTTP@/$1 [P,L]

  ProxyPreserveHost On
  ProxyRequests Off

  ProxyPass / https://127.0.0.1:@PORT_HTTPS@/ nocanon
  ProxyPassReverse / https://127.0.0.1:@PORT_HTTPS@/

  SSLEngine On
  SSLProxyEngine On
  SSLProxyVerify none
  SSLProxyCheckPeerCN off
  SSLProxyCheckPeerName off
  SSLProxyCheckPeerExpire off

  SSLCertificateFile @CERT_FILE@
  SSLCertificateKeyFile @KEY_FILE@

  ErrorLog /var/log/apache2/@SITE@-443-error.log
</VirtualHost>
