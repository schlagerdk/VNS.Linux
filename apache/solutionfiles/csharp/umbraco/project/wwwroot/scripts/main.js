function consoleObj(obj){ $.each(obj, function(key, element) { console.log('key: ' + key + '\n' + 'value: ' + element); }); }
function getCookie(name){try{if(document.cookie.length > 0){c_start = document.cookie.indexOf(name + '=');if(c_start !== -1){c_start = c_start + name.length + 1;c_end = document.cookie.indexOf(';', c_start);if(c_end === -1){c_end = document.cookie.length;} return unescape(document.cookie.substring(c_start, c_end));}}}catch(err){} return '';}
function setCookie(name, value, days){try{var expires;if(days){var date = new Date();date.setTime(date.getTime() + (days * 24 * 60 * 60 * 1000));expires = ';expires=' + date.toGMTString();}else{expires = '';}document.cookie = name + '=' + value + expires + '; path=/';}catch(err){}}
function getDevice(){var tag = window.getComputedStyle(document.body,':after').getPropertyValue('content'); tag = tag.replace( /"/g,''); return tag;}
function jQueryAjax(){$.ajax({url:'URL', cache:false, async:false}).done(function(data){}).fail(function(data){}).always(function(data){});}
var youtubeekstra = 'rel=0&amp;showinfo=0&amp;controls=1&amp;disablekb=1&amp;hd=1&amp;autohide=1&autoplay=0';

// Get Video ID from Youtube or Vimeo Url
function getVDOID(url) {
    try{
   		var match = url.match(/(?:youtu\.be\/|youtube\.com(?:\/embed\/|\/v\/|\/watch\?v=|\/user\/\S+|\/ytscreeningroom\?v=))([\w\-]{10,12})\b/);

   		if (match && match[1].length == 11) { return match[1]; }
    		
		match = url.match("(https?://)?(www.)?(player.)?vimeo.com/([a-z]*/)*([0-9]{6,11})[?]?.*");
    		
   		if (match) { return url.split('/')[url.split('/').length - 1]; }
    
   		return url;
    }catch(e){}
}

// Set Video ID from Youtube or Vimeo Url
function setVDOID(obj) { try{ $(obj).attr('src', function(i, val){ return val.replace('##ID##', getVDOID($(this).attr('url'))); }); } catch(e){} }

// Window Resize
$(window).resize(function(){});

// Document Loadad
$(function(){

	// Flexmenu
    setTimeout(function() { $('.mainmenu').flexMenu({ linkText: '<i class="bi bi-list"></i>', linkTitle: 'Vis Menu', linkTextAll: '<i class="bi bi-list"></i>', linkTitleAll: 'Vis menu', threshold: 0, cutoff: 1 }); }, 200);	
	
	// Match Height
	try{ $('.match').matchHeight(); }catch(e){}
	
    // Custom Select
    try{
        $('.customselect').on('change', function(){ $(this).valid(); });
        $('.customselect').select2({ matcher: function(params, data) { return matchStart(params, data); } });
        $('.customselect').on('select2:open',function(){$('.select2-search__field').attr('placeholder','Søg...');});
        $('.customselect').on('select2:open',function(){$('.select2-selection--multiple .select2-search__field').attr('placeholder',null);});
        $('.customselect').on('select2:close',function(){$('.select2-search__field').attr('placeholder',null);});
        $('.customselect').on('select2:close',function(){$('.select2-selection--multiple .select2-search__field').attr('placeholder','Søg...').blur();});
        $('.customselect.nosearch').select2({minimumResultsForSearch: -1});
        $('.select2').css('width', '100%');
        $('.select2-selection--multiple .select2-search__field').attr('placeholder','Søg...');
    } catch(e){}
	
	// Fix youtube and vimeo
	try{
		$('iframe').attr('src', function(i, val){ 
		    if(!val){ 
    		    if($(this).attr('url').indexOf('vimeo.com') !== -1) { return 'https://player.vimeo.com/video/##ID##?autoplay=0'; }
    		    if($(this).attr('url').indexOf('youtube.com') !== -1) { return 'https://www.youtube.com/embed/##ID##?'+ youtubeekstra; } 
		    } else if(val.indexOf('youtube.com') != -1) { if(val.indexOf('?') != -1) { return val + '&amp;' + youtubeekstra; } else { return val + '?' + youtubeekstra; } }
		});
		$('iframe').attr('src', function(i, val){ if(val){ if(val.indexOf('youtube.com') !== -1 || val.indexOf('vimeo.com') !== -1){ $(this).wrap('<p class="vdowrap"></p>'); if(val.indexOf('vimeo.com') !== -1){ $(this).parent().css('margin', '-13px 0px'); } } } });
		
		setVDOID($('.ifVideo'));
	}catch(e){}
	
	// Attach datepicker
	try{
    	$('.datepicker').datepicker({
    		dayNamesMin: ['Sø', 'Ma', 'Ti', 'On', 'To', 'Fr', 'Lø'],
      		monthNames: ['Januar', 'Februar', 'Marts', 'April', 'Maj', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'December'],
    		dateFormat: 'dd-mm-yy',
    		firstDay: 1
    	});
	} catch(e){}
	
	// Validate RegExp
	$.validator.addMethod('email', function (value, element){ if(value === '') { return true; } return value.match(/^[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,4}$/);});
	$.validator.addMethod('phone', function (value, element){ if(value === '') { return true; } return value.match(/^(\(?\Q+45\E\)?( )?)?[0-9]{8}$/);});
	
	// Validate
	$('#frmNews').validate({
	    rules:{txtEmail:{email:true}},
	    messages: {
		    txtName: '<div class="errormsg"><span class="arrow"></span>Angiv dit for- og efternavn</div>',
			txtEmail: '<div class="errormsg"><span class="arrow"></span>Angiv din email</div>'
		},
		errorClass:'error',
		validClass:'',
		highlight: function (element, errorClass, validClass){$(element).addClass(errorClass);},
		unhighlight: function (element, errorClass, validClass){$(element).removeClass(errorClass);},
		invalidHandler: function (event, validator){var errors = validator.numberOfInvalids();},
		submitHandler: function(){}
	});
});