WebApplicationBuilder builder = WebApplication.CreateBuilder(args);

builder.CreateUmbracoBuilder()
    .AddBackOffice()
    .AddWebsite()
    .AddDeliveryApi()
    .AddComposers()
    .Build();

WebApplication app = builder.Build();

if (app.Environment.IsDevelopment() || app.Environment.IsEnvironment("Test")){
	app.UseDeveloperExceptionPage();
} else {
	app.UseExceptionHandler("/error500");
}

app.Use(async (context, next) => {
	context.Response.Headers.Append("X-Frame-Options", "SAMEORIGIN");
	context.Response.Headers.Append("X-Content-Type-Options", "nosniff");
	context.Response.Headers.Append("Strict-Transport-Security", "max-age=31536000");
	context.Response.Headers.Append("Content-Security-Policy",
	    "default-src 'self'; " +
	    "script-src 'self' https: 'unsafe-inline' 'report-sample'; " +
	    "style-src 'self' https: 'unsafe-inline' 'report-sample'; " +
	    "img-src * data:; " +
	    "font-src 'self' https: data:; " +
	    "connect-src *; " +
	    "frame-src *; " +
	    "object-src 'none'; " +
	    "base-uri 'self'; " +
	    "form-action 'self' https:; " +
	    "report-uri https://vns.dk/#contact;"
	);
	await next();
});

await app.BootUmbracoAsync();

app.UseUmbraco()
    .WithMiddleware(u =>
    {
        u.UseBackOffice();
        u.UseWebsite();
    })
    .WithEndpoints(u =>
    {
        u.UseBackOfficeEndpoints();
        u.UseWebsiteEndpoints();
    });

await app.RunAsync();
